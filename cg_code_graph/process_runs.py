"""Programs a test starts in a subprocess, in languages other than Python (#60; Python: plugins/python/subproc.py).

A source scan over test code (and helpers) finds process starts whose program is the project's own entry point and
adds CALLS (attrs.via = "subprocess", attrs.how) from the enclosing function / test to it; `isolate_tests` turns them
into TEST_CALLS, so `cg tests` counts the test for what the program reaches:

  * Rust: `env!("CARGO_BIN_EXE_x")`, `Command::cargo_bin("x")` / `cargo_bin!("x")` (assert_cmd, escargot) ->
    the `main` of bin target `x` (Cargo.toml, auto-discovered `src/main.rs` / `src/bin/*`); `CARGO_PKG_NAME` -> the
    bin named like the test's package;
  * Node: `spawn / spawnSync / execFile / execFileSync / fork / exec / execSync` (child_process) and
    `execa / execaSync / execaNode / execaCommand` running `node | tsx | ts-node | bun | process.execPath` with a
    project script, `fork(script)`, or a `package.json` `bin` name -> that script's module node;
  * PHP: `new Process(['php', 'artisan', 'x'])`, `Process::run('php artisan x')`, `exec / shell_exec / system /
    passthru('php artisan x')` -> the artisan command node (`$this->artisan()` and `Artisan::call()` are modelled by
    the Laravel plugin);
  * Dart: `Process.run / runSync / start('dart', ['run'?, 'bin/x.dart', ...])`, `TestProcess.start(...)`
    (`Platform.resolvedExecutable` as the program too) -> `main` of that file.
Go has no language plugin, so `exec.Command(os.Args[0])` / `go run ./cmd/x` are not linked. Names passed in the call
are read from their last assignment earlier in the same function (`var script = p.join(dir, '../bin/x.dart')`);
values built in other functions or from configuration are not followed (the Python layer evaluates further).
"""
from __future__ import annotations

import json
import re
import shlex
from bisect import bisect_right
from collections import defaultdict

from .core.model import RESOLVED

JS_EXT = (".js", ".mjs", ".cjs", ".ts", ".mts", ".cts", ".jsx", ".tsx")
SCAN_EXT = JS_EXT + (".rs", ".php", ".dart")
LIT = re.compile(r"""(?P<q>['"`])(?P<s>(?:\\.|(?!(?P=q)).)*?)(?P=q)""", re.S)
RUST_RX = re.compile(r'CARGO_BIN_EXE_([A-Za-z0-9_\-]+)|cargo_bin!?\s*\(\s*(?:"([^"]+)"|env!\s*\(\s*"CARGO_PKG_NAME"\s*\))')
JS_RX = re.compile(r"\b(spawn|spawnSync|execFile|execFileSync|fork|exec|execSync|execa|execaSync|execaNode|"
                   r"execaCommand|execaCommandSync)\s*\(")
PHP_RX = re.compile(r"new\s+Process\s*\(|Process::(?:run|start|pipe)\s*\(|\b(?:exec|shell_exec|system|passthru)\s*\(")
DART_RX = re.compile(r"\b(?:Process\.(?:run|runSync|start)|TestProcess\.start)\s*\(")
JS_RUNTIMES = {"node", "nodejs", "tsx", "ts-node", "bun", "deno"}


def _args_text(src: str, start: int, limit: int = 600) -> str:
    """Text of the call's argument list from the `(` at `start` (balanced parentheses / brackets)."""
    depth, i, n = 0, start, min(len(src), start + limit)
    while i < n:
        c = src[i]
        if c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
            if depth == 0:
                return src[start + 1:i]
        elif c in "'\"`":
            m = LIT.match(src, i)
            if m:
                i = m.end()
                continue
        i += 1
    return src[start + 1:n]


def _lits(text: str) -> list[str]:
    return [m.group("s") for m in LIT.finditer(text)]


class _Scan:
    def __init__(self, project, b):
        self.root, self.b = project.root, b
        self.st = defaultdict(int)
        self.samples = defaultdict(list)
        self.by_file = defaultdict(list)          # file -> [(line, end_line, nid)] for functions / tests
        self.modules = {}                          # file -> module node id
        self.mains = {}                            # file -> main function id
        self.commands = {}                         # artisan command name -> node id
        for nid, n in b.nodes.items():
            if not n.file:
                continue
            if n.kind == "module":
                self.modules.setdefault(n.file, nid)
            elif n.kind in ("function", "method", "test") and n.line:
                self.by_file[n.file].append((n.line, n.end_line or n.line, nid))
                if n.name == "main" or nid.endswith("#main") or nid.endswith("::main"):
                    if n.kind == "function":
                        self.mains.setdefault(n.file, nid)
            elif n.kind == "command":
                self.commands[n.name] = nid
        for f in self.by_file.values():
            f.sort()
        self.files = sorted(self.modules) + sorted(set(self.mains) - set(self.modules))

    # ---------------------------------------------------------------- shared
    def enclosing(self, file, line):
        best = None
        for ln, end, nid in self.by_file.get(file, ()):
            if ln <= line <= end and (best is None or end - ln < best[0]):
                best = (end - ln, nid)
        return best[1] if best else self.modules.get(file)

    def assigned_lits(self, file, src, pos, args) -> list[str]:
        """Literals assigned to the names the call passes (`var script = p.join(dir, '../bin/x.dart');` earlier in
        the same function, else a module-level constant above it): the last assignment before the call. Values built
        in other functions are not followed."""
        line = src.count("\n", 0, pos) + 1
        start = 1
        for ln, end, _nid in self.by_file.get(file, ()):
            if ln <= line <= end and ln > start:
                start = ln
        lo = 0
        for _ in range(start - 1):
            lo = src.find("\n", lo) + 1
        body, out = src[lo:pos], []
        for ident in dict.fromkeys(re.findall(r"(?<![\w.$])([A-Za-z_]\w*)\b(?!\s*[(.])", LIT.sub("", args))):
            rx = re.compile(rf"(?<![\w.$]){re.escape(ident)}\s*=(?!=)\s*([^;]+);")
            ms = list(rx.finditer(body)) or list(rx.finditer(src[:lo]))   # else a module-level constant above
            if ms:
                out += _lits(ms[-1].group(1))
        return out

    def add(self, file, line, dst, how, cmd):
        src = self.enclosing(file, line)
        if src is None or src == dst:
            return
        self.b.add_edge(src, dst, "CALLS", file, line, RESOLVED, via="subprocess", how=how, command=cmd[:120])
        self.st["linked"] += 1
        self.st[f"linked_{how.replace(' ', '_')}"] += 1

    def miss(self, key, text):
        self.st[key] += 1
        if text and len(self.samples[key]) < 5 and text not in self.samples[key]:
            self.samples[key].append(text[:80])

    def resolve_path(self, file: str, text: str, pool) -> str | None:
        """Project file a script path names: `./` / `../` paths relative to the calling file's directory
        (`path.join(__dirname, '../bin/cli.js')`) or the root, other paths root-relative, else a unique suffix match
        among `pool` (files with a node)."""
        import posixpath
        t = text.replace("\\", "/")
        cands = []
        if t.startswith(("./", "../")):
            cands.append(posixpath.normpath(posixpath.join(posixpath.dirname(file), t)))
        cands.append(posixpath.normpath(t.lstrip("/")))
        for c in cands:
            if not c.startswith("..") and (self.root / c).is_file():
                return c
        if t.startswith("../"):
            return None                         # outside the caller's tree: not guessed by suffix
        return self.by_suffix(t, pool)

    def by_suffix(self, tail: str, pool) -> str | None:
        tail = tail.replace("\\", "/").lstrip("./")
        while tail.startswith("../"):
            tail = tail[3:]
        if not tail:
            return None
        hits = [f for f in pool if f == tail or f.endswith("/" + tail)]
        return hits[0] if len(hits) == 1 else None

    # ---------------------------------------------------------------- Rust
    def rust_bins(self) -> dict:
        try:
            from .plugins.rust.cargo import from_toml
            pkgs = from_toml(self.root)
        except Exception:
            pkgs = []
        bins, pkg_dirs = {}, []
        for p in pkgs:
            pkg_dirs.append((p.dir, p.name))
            for t in p.targets:
                if t.kind == "bin":
                    bins.setdefault(t.name, t.src)
        return bins, sorted(pkg_dirs, key=lambda x: -len(x[0]))

    def rust(self, file, src):
        if not hasattr(self, "_bins"):
            self._bins = self.rust_bins()
        bins, pkg_dirs = self._bins
        for m in RUST_RX.finditer(src):
            name = m.group(1) or m.group(2)
            if name is None:      # env!("CARGO_PKG_NAME"): the package this test belongs to
                name = next((n for d, n in pkg_dirs if not d or file.startswith(d.rstrip("/") + "/")), None)
            line = src.count("\n", 0, m.start()) + 1
            path = bins.get(name or "") or bins.get((name or "").replace("-", "_"))
            dst = self.mains.get(path) if path else None
            if dst is None:
                self.miss("unresolved", f"bin {name}")
                continue
            self.add(file, line, dst, "cargo bin", f"cargo bin {name}")

    # ---------------------------------------------------------------- Node
    def js_bins(self) -> dict:
        out = {}
        for pj in [self.root / "package.json"] + sorted(self.root.glob("packages/*/package.json")):
            try:
                d = json.loads(pj.read_text())
            except Exception:
                continue
            rel = "" if pj.parent == self.root else pj.parent.relative_to(self.root).as_posix() + "/"
            b = d.get("bin")
            if isinstance(b, str) and d.get("name"):
                out[d["name"].split("/")[-1]] = rel + b.lstrip("./")
            elif isinstance(b, dict):
                for k, v in b.items():
                    out[k] = rel + str(v).lstrip("./")
        return out

    def js_script(self, lits, file="") -> str | None:
        """Project script among the call's literals: the first one with a JS extension, with the plain path segments
        before it (`path.join(__dirname, '..', 'bin', 'cli.js')`)."""
        js_files = [f for f in self.modules if f.endswith(JS_EXT)]
        for i, s in enumerate(lits):
            if s.endswith(JS_EXT) and not s.startswith("-"):
                segs = [x for x in lits[max(0, i - 3):i] if re.fullmatch(r"[\w.\-/]+", x) and not x.startswith("-")]
                for k in range(len(segs), -1, -1):
                    got = self.resolve_path(file, "/".join(segs[len(segs) - k:] + [s]), js_files)
                    if got:
                        return got
                return None
        return None

    def js(self, file, src):
        if not hasattr(self, "_jsbins"):
            self._jsbins = self.js_bins()
        for m in JS_RX.finditer(src):
            fn = m.group(1)
            line = src.count("\n", 0, m.start()) + 1
            args = _args_text(src, m.end() - 1)
            lits = _lits(args)
            if fn in ("exec", "execSync", "execaCommand", "execaCommandSync"):
                if not lits:
                    continue
                try:
                    lits = shlex.split(lits[0]) + lits[1:]
                except ValueError:
                    lits = lits[0].split() + lits[1:]
            prog = "process.execPath" if args.lstrip().startswith("process.execPath") else (lits[0] if lits else "")
            rest = lits if fn in ("fork", "execaNode") else lits[1:] if prog != "process.execPath" else lits
            rest = rest + self.assigned_lits(file, src, m.start(), args)
            base = re.split(r"[/\\]", prog)[-1]
            target = None
            if fn in ("fork", "execaNode") or prog == "process.execPath" or base in JS_RUNTIMES or base == "npx":
                if base == "npx" and rest and rest[0] in self._jsbins:
                    target = self._jsbins[rest[0]]
                else:
                    target = self.js_script([x for x in rest if x not in ("run", "--")], file)
            elif base in self._jsbins:
                target = self._jsbins[base]
            elif prog.endswith(JS_EXT):
                target = self.js_script(lits, file)
            else:
                continue                      # git, docker, ...: not a project program
            dst = self.modules.get(target) if target else None
            if dst is None:
                self.miss("script_without_node" if target else "unresolved",
                          target or f"{fn}({' '.join(lits[:3])})")
                continue
            self.add(file, line, dst, "node script" if base not in self._jsbins else "package bin",
                     " ".join(lits[:6]))

    # ---------------------------------------------------------------- PHP
    def php(self, file, src):
        for m in PHP_RX.finditer(src):
            line = src.count("\n", 0, m.start()) + 1
            lits = _lits(_args_text(src, m.end() - 1))
            if not lits:
                continue
            if len(lits) == 1 and " " in lits[0]:
                try:
                    lits = shlex.split(lits[0])
                except ValueError:
                    lits = lits[0].split()
            words = [re.split(r"[/\\]", x)[-1] for x in lits]
            if "artisan" not in words:
                continue
            i = words.index("artisan")
            name = next((w for w in lits[i + 1:] if not w.startswith("-")), None)
            dst = self.commands.get(name or "")
            if dst is None:
                self.miss("unresolved", f"artisan {name}")
                continue
            self.add(file, line, dst, "artisan", " ".join(lits[:6]))

    # ---------------------------------------------------------------- Dart
    def dart(self, file, src):
        dart_files = list(self.mains)
        for m in DART_RX.finditer(src):
            line = src.count("\n", 0, m.start()) + 1
            args = _args_text(src, m.end() - 1)
            lits = _lits(args)
            if not (args.lstrip().startswith("Platform.resolvedExecutable") or (lits and lits[0] in ("dart", "flutter"))):
                continue
            script = next((x for x in lits + self.assigned_lits(file, src, m.start(), args) if x.endswith(".dart")), None)
            path = self.resolve_path(file, script, dart_files) if script else None
            dst = self.mains.get(path) if path else None
            if dst is None:
                self.miss("unresolved", " ".join(lits[:4]))
                continue
            self.add(file, line, dst, "dart script", " ".join(lits[:6]))

    def run(self) -> dict:
        files = set(self.modules) | set(self.by_file)
        for file in sorted(files):
            if not file.endswith(SCAN_EXT):          # files of graph nodes: the walks already skipped presets' dirs
                continue
            try:
                src = (self.root / file).read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if file.endswith(".rs"):
                if "cargo_bin" in src or "CARGO_BIN_EXE_" in src:
                    self.rust(file, src)
            elif file.endswith(".php"):
                if "artisan" in src:
                    self.php(file, src)
            elif file.endswith(".dart"):
                if "Process" in src:
                    self.dart(file, src)
            elif ("child_process" in src or "execa" in src) and JS_RX.search(src):
                self.js(file, src)
        out = {k: v for k, v in self.st.items() if v}
        if self.samples:
            out["samples"] = dict(self.samples)
        return out


def apply(project, builder) -> dict:
    """Add the subprocess edges; returns stats (empty when nothing was found)."""
    return _Scan(project, builder).run()


# ------------------------------------------------------------------ child processes as endpoints (#38 part 3)
PROGRAM_HOWS = {"-m", "script", "console script", "node script", "package bin", "cargo bin", "artisan", "dart script",
                "copied script"}


def program_name(n) -> str:
    """The endpoint name of a program a process start runs: its file (a module / script / `main`), else its name
    (console scripts, artisan commands)."""
    a = n.attrs or {}
    if n.kind == "script" and a.get("group") in ("console_scripts", "gui_scripts"):
        return n.name
    if n.kind == "command":
        return f"artisan {n.name}"
    return n.file or n.name


def process_endpoints(builder) -> dict:
    """Process starts in application code (the CALLS with `via: subprocess` that plugins/python/subproc.py and the
    scan above add) as `endpoint:process:<program>`: SENDS_TO from the function that starts it, RECEIVED_BY the
    program's entry (`__main__` block, module, `main`, command). Starts in tests stay TEST_CALLS only (running the CLI
    under test is not a process talking to another); `python -c` snippets are calls, not programs."""
    from .brokers import TEST_FILE
    from .protocols import protocol_receive, protocol_send
    from .tests_index import is_test_node
    st = defaultdict(int)
    done = set()
    for e in list(builder.edges.values()):
        a = e.attrs or {}
        if e.kind != "CALLS" or a.get("via") != "subprocess" or a.get("how") not in PROGRAM_HOWS:
            continue
        src, dst = builder.nodes.get(e.src), builder.nodes.get(e.dst)
        if src is None or dst is None or is_test_node(src) or TEST_FILE.search(e.file or src.file or ""):
            continue
        name = program_name(dst)
        key = (e.src, name, e.line)
        if key in done:
            continue
        done.add(key)
        protocol_send(builder, "process", name, e.src, e.file, e.line, e.confidence, role="spawn", how=a.get("how"),
                      command=a.get("command"))
        st["spawns"] += 1
        if ("r", name) not in done:
            done.add(("r", name))
            protocol_receive(builder, "process", name, e.dst, dst.file, dst.line, e.confidence, how="program entry")
            st["programs"] += 1
    return {k: v for k, v in st.items() if v}
