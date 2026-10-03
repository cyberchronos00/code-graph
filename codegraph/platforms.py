"""Platform-specific code and multi-target builds: platform tags, the --platform filter and the divergence query.

One source tree often builds several targets (Windows / Linux / macOS from one Rust or C code base, iOS / Android /
web from one Flutter or React Native app). The index records which targets each piece of code exists on:

  node.attrs / edge.attrs   platforms         targets the code (or the reference) exists on; absent = every target
                            platform_expr     the source condition(s), e.g. `cfg(windows)`, `#if defined(__APPLE__)`
                            platform_at       file:line of the condition
                            platform_unknown  targets where part of the condition could not be evaluated (kept live)

Each language layer adds the conditions of its own idioms (see docs/platforms.md):

  Rust        #[cfg(...)] on items, `mod` declarations, statements and `#![cfg]`; `if cfg!(...)` branches
  C / C++     #if / #ifdef / #elif regions over the platform macros (_WIN32, __APPLE__ + TARGET_OS_*, __linux__,
              __ANDROID__, __EMSCRIPTEN__, BSDs); platform directories and file names (src/win/, unix/, foo_linux.c)
  Dart        conditional imports and exports (`import 'a.dart' if (dart.library.io) 'b.dart'`),
              Platform.isX / kIsWeb / defaultTargetPlatform branches, ternaries and switches
  TypeScript  React Native / Expo platform files (.ios.ts, .android.ts, .native.ts, .web.ts, also tsx / js / jsx),
              Platform.OS checks, Platform.select({...}), Node / Electron process.platform checks
  any         .cg.yaml platforms.paths globs

A condition is evaluated three-valued for every known target (True / False / unknown): a part cg cannot evaluate
(a feature flag, `HAVE_SOUND`, an arch check) keeps the code live on that target and is listed as unknown.

Variant implementations (one symbol defined per platform: `#[cfg(windows)] fn config_dir` + `#[cfg(unix)] fn
config_dir`, storage.ios.ts + storage.android.ts, a conditional-import stub and its io / web libraries) are grouped:
every reference to one variant also reaches the others, each tagged with its own platforms, so `impact` / `reaches`
on any target follow the implementation that target builds.
"""
from __future__ import annotations

import fnmatch
import json
import re
from collections import defaultdict
from pathlib import Path
from . import presets

# dependency / VCS directories every walk skips (codegraph/presets/common.yaml skip_dirs)
COMMON_SKIP = presets.skip_dirs("common")

KNOWN = ("windows", "linux", "macos", "ios", "android", "web")
DESKTOP = ("windows", "linux", "macos")
ALIASES = {"win": "windows", "win32": "windows", "win64": "windows", "darwin": "macos", "mac": "macos", "osx": "macos",
           "macosx": "macos", "iphoneos": "ios", "iphone": "ios", "browser": "web", "wasm": "web", "wasm32": "web",
           "js": "web", "emscripten": "web"}
UNIX = ("linux", "macos", "ios", "android")
BIG = 10 ** 9


def norm(name: str | None) -> str | None:
    """'Darwin' -> 'macos', 'win32' -> 'windows', 'ios' -> 'ios'; None for a name that is not a known target."""
    if not name:
        return None
    n = str(name).strip().lower()
    n = ALIASES.get(n, n)
    return n if n in KNOWN else None


def resolve_platform(name: str) -> str:
    """A --platform value -> known target name; ValueError naming the known targets otherwise."""
    p = norm(name)
    if p is None:
        raise ValueError(f"unknown platform {name!r}: known targets are {', '.join(KNOWN)} "
                         f"(aliases: {', '.join(sorted(ALIASES))})")
    return p


# ------------------------------------------------------------------ evaluation: cfg-style trees
DART_NATIVE_LIBS = {"io", "ffi", "isolate", "cli", "developer", "nativewrappers", "mirrors"}
DART_WEB_LIBS = {"html", "js", "js_util", "js_interop", "js_interop_unsafe", "web_audio", "web_gl", "indexed_db", "svg",
                 "web_sql"}


def eval_atom(key: str, val: str | None, p: str):
    """One condition atom on target p: True / False / None (unknown)."""
    if key == "platform":                         # Platform.OS === 'ios', Platform.isIOS, kIsWeb, file suffixes
        v = ALIASES.get((val or "").lower(), (val or "").lower())
        return p == v
    if key == "native":                           # React Native `.native.ts` / Platform.select native: every non-web target
        return p != "web"
    if key == "target_platform":                  # Flutter TargetPlatform: on the web it is the browser's OS
        return None if p == "web" else p == val
    if key == "dart_library":
        if val in DART_NATIVE_LIBS:
            return p != "web"
        if val in DART_WEB_LIBS:
            return p == "web"
        return None
    if key == "unix":
        return p in UNIX
    if key == "windows":
        return p == "windows"
    if key == "target_os":
        v = ALIASES.get(val or "", val or "")
        if v in ("emscripten", "unknown", "wasi", "none"):
            return None if p == "web" else False
        return p == v
    if key == "target_family":
        if val == "unix":
            return p in UNIX
        if val == "windows":
            return p == "windows"
        if val == "wasm":
            return p == "web"
        return None
    if key == "target_vendor":
        if val == "apple":
            return p in ("macos", "ios")
        return None
    if key == "target_arch":
        if val in ("wasm32", "wasm64"):
            return p == "web"
        return False if p == "web" else None
    if key == "target_env" and val == "msvc":
        return None if p == "windows" else False
    return None


def eval_tree(t, p: str):
    k = t[0]
    if k == "atom":
        return eval_atom(t[1], t[2], p)
    if k == "not":
        v = eval_tree(t[1], p)
        return None if v is None else (not v)
    vals = [eval_tree(x, p) for x in t[1]]
    if k == "all":
        if any(v is False for v in vals):
            return False
        return True if all(v is True for v in vals) else None
    if any(v is True for v in vals):
        return True
    return False if vals and all(v is False for v in vals) else None


# ------------------------------------------------------------------ evaluation: C preprocessor
# macro -> value per target ("1" / "0" defined with that value, None = may or may not be defined); every macro in
# C_MACROS that a target does not list is undefined there
C_FACTS: dict[str, dict[str, str | None]] = {
    "windows": {"_WIN32": "1", "WIN32": "1", "__WIN32__": "1", "_WINDOWS": None, "_WIN64": None, "_MSC_VER": None,
                "__MINGW32__": None, "__MINGW64__": None, "__CYGWIN__": None, "WINAPI_FAMILY": None},
    "linux": {"__linux__": "1", "__linux": "1", "linux": None, "__gnu_linux__": "1", "__unix__": "1", "__unix": "1",
              "unix": None, "__GLIBC__": None},
    "android": {"__linux__": "1", "__linux": "1", "linux": None, "__ANDROID__": "1", "ANDROID": None, "__BIONIC__": "1",
                "__unix__": "1", "__unix": "1", "unix": None, "__ANDROID_API__": None},
    "macos": {"__APPLE__": "1", "__MACH__": "1", "TARGET_OS_MAC": "1", "TARGET_OS_OSX": "1", "TARGET_OS_IPHONE": "0",
              "TARGET_OS_IOS": "0", "TARGET_OS_SIMULATOR": "0", "TARGET_IPHONE_SIMULATOR": "0", "TARGET_OS_TV": "0",
              "TARGET_OS_WATCH": "0", "TARGET_OS_VISION": "0", "TARGET_OS_MACCATALYST": "0", "__APPLE_CC__": None},
    "ios": {"__APPLE__": "1", "__MACH__": "1", "TARGET_OS_MAC": "1", "TARGET_OS_OSX": "0", "TARGET_OS_IPHONE": "1",
            "TARGET_OS_IOS": "1", "TARGET_OS_SIMULATOR": None, "TARGET_IPHONE_SIMULATOR": None, "TARGET_OS_TV": "0",
            "TARGET_OS_WATCH": "0", "TARGET_OS_VISION": "0", "TARGET_OS_MACCATALYST": None, "__APPLE_CC__": None},
    "web": {"__EMSCRIPTEN__": "1", "EMSCRIPTEN": None, "__wasm__": "1", "__wasm32__": None, "__wasm64__": None,
            "__unix__": "1", "__unix": "1", "unix": None},
}
C_OTHER_OS = ("__FreeBSD__", "__OpenBSD__", "__NetBSD__", "__DragonFly__", "__sun", "__sun__", "_AIX", "__HAIKU__",
              "__Fuchsia__", "__QNX__", "__CYGWIN32__", "__MSYS__", "__hpux", "__GNU__", "__OS400__", "__VMS", "__MVS__",
              "__ZEPHYR__", "__redox__")
C_MACROS = set(C_OTHER_OS) | {m for f in C_FACTS.values() for m in f}


def eval_c(expr: str, p: str):
    from .plugins.native.gates import eval_pp
    facts = C_FACTS[p]
    defines = {m: v for m, v in facts.items() if v is not None}
    undefined = {m for m in C_MACROS if m not in facts}
    return eval_pp(expr, defines, undefined)


def c_relevant(expr: str) -> bool:
    return any(m in C_MACROS for m in re.findall(r"[A-Za-z_]\w*", expr))


# ------------------------------------------------------------------ conditions and marks
class Cond:
    """A platform condition: kind 'tree' (cfg-style tree) or 'c' (preprocessor expression), with its source text."""
    __slots__ = ("kind", "expr", "text", "_v")

    def __init__(self, kind: str, expr, text: str):
        self.kind, self.expr, self.text = kind, expr, text
        self._v = None

    def values(self) -> dict[str, bool | None]:
        if self._v is None:
            if self.kind == "c":
                self._v = {p: eval_c(self.expr, p) for p in KNOWN}
            else:
                self._v = {p: eval_tree(self.expr, p) for p in KNOWN}
        return self._v

    def relevant(self) -> bool:
        """Does the condition tell targets apart (some target False, or True on some and unknown on others)?"""
        v = self.values()
        if any(x is False for x in v.values()):
            return True
        return False


def cfg_cond(pred: str, text: str | None = None) -> Cond:
    from .plugins.native.gates import parse_cfg
    return Cond("tree", parse_cfg(pred), text or f"cfg({pred})")


def mark(builder, file: str, start: int, end: int, cond: Cond, line: int | None = None, scol: int | None = None,
         ecol: int | None = None, nodes: bool = True) -> None:
    """Record that code in file[start:end] (1-based lines, inclusive; optional 0-based columns on the first / last
    line) exists only where `cond` holds. Applied to nodes defined there (nodes=True) and references made there."""
    if not cond.relevant():
        return
    builder.platform_marks.append({"file": file, "start": start, "end": end, "scol": scol, "ecol": ecol, "cond": cond,
                                   "line": line or start, "nodes": nodes})


def _combine(vals: list[dict]) -> dict:
    out = {}
    for p in KNOWN:
        xs = [v[p] for v in vals]
        out[p] = False if any(x is False for x in xs) else (True if all(x is True for x in xs) else None)
    return out


# ------------------------------------------------------------------ file conventions
RN_SUFFIX_RE = re.compile(r"^(?P<stem>.+?)\.(?P<plat>ios|android|native|web|windows|macos)\.(?P<ext>[cm]?[jt]sx?)$")
RN_DEPS = ("react-native", "expo", "react-native-web", "react-native-windows", "react-native-macos")
C_EXT = re.compile(r"\.(c|h|cc|cpp|cxx|hh|hpp|hxx|ipp|inl|m|mm)$")
PATH_DIRS = {"win": "windows", "win32": "windows", "windows": "windows", "unix": "unix", "posix": "unix",
             "linux": "linux", "darwin": "macos", "macos": "macos", "osx": "macos", "mac": "macos", "ios": "ios",
             "android": "android", "emscripten": "web", "wasm": "web"}
C_SOURCE_EXT = {"c", "cc", "cpp", "cxx", "m", "mm"}
PATH_STEM = re.compile(r"(?:^|[_-])(win32|win|windows|unix|posix|linux|darwin|macos|osx|ios|android|emscripten)$", re.I)
# files named after an OS outside the known targets (src/unix/aix.c, os390.c): built for none of them
OTHER_OS_STEM = re.compile(r"(?:^|[_-])(aix|sunos|solaris|os390|zos|ibmi|freebsd|openbsd|netbsd|dragonfly|haiku|qnx|"
                           r"cygwin|hurd|illumos|pase)$", re.I)


def _plat_atom(name: str):
    if name == "other":                            # an OS outside the known targets
        return ("atom", "platform", "__other__")
    if name == "unix":
        return ("atom", "unix", None)
    if name == "native":
        return ("atom", "native", None)
    return ("atom", "platform", name)


def path_convention(rel: str) -> tuple[str, str] | None:
    """C / C++ platform directories and file names: ('windows', 'directory win/') for src/win/fs.c."""
    parts = rel.split("/")
    stem, _, ext = parts[-1].rpartition(".")
    if ext.lower() in C_SOURCE_EXT:      # a header named after a platform (macos.h) is usually included everywhere
        if OTHER_OS_STEM.search(stem):
            return "other", f"file name {parts[-1]}"
        m = PATH_STEM.search(stem)
        if m and not (m.group(1).lower() in ("unix", "posix") and any(d.lower() in PATH_DIRS for d in parts[:-1])):
            w = m.group(1).lower()       # src/unix/linux.c: the file name is narrower than its directory
            return PATH_DIRS.get(w, w), f"file name {parts[-1]}"
    for d in parts[:-1]:
        if d.lower() in PATH_DIRS:
            return PATH_DIRS[d.lower()], f"directory {d}/"
    return None


def uses_react_native(root: Path) -> bool:
    for pj in [root / "package.json", *root.glob("*/package.json"), *root.glob("*/*/package.json")]:
        if not COMMON_SKIP.isdisjoint(pj.parts) or not pj.is_file():
            continue
        try:
            d = json.loads(pj.read_text(encoding="utf-8", errors="replace"))
        except (ValueError, OSError):
            continue
        deps = {**(d.get("dependencies") or {}), **(d.get("devDependencies") or {}), **(d.get("peerDependencies") or {})}
        if any(k in deps for k in RN_DEPS):
            return True
    return False


# ------------------------------------------------------------------ targets of the project
def declared_targets(root: Path, cfg: dict, marks: list, langs: set[str]) -> tuple[list[str], dict[str, str]]:
    """Targets the project builds: .cg.yaml platforms.targets, else Flutter platform folders, the Expo / React
    Native config, Electron / Tauri (desktop), else the desktop targets plus every target a condition names on its
    own (target_os = "android", __EMSCRIPTEN__). Returns (targets, target -> where it came from)."""
    pc = (cfg or {}).get("platforms") or {}
    if pc.get("targets"):
        return list(pc["targets"]), {t: cfg.get("file", ".cg.yaml") for t in pc["targets"]}
    src: dict[str, str] = {}
    for pub in [root / "pubspec.yaml", *root.glob("*/pubspec.yaml"), *root.glob("*/*/pubspec.yaml")]:
        if not pub.is_file() or "flutter" not in pub.read_text(encoding="utf-8", errors="replace"):
            continue
        for d in ("android", "ios", "web", "macos", "windows", "linux"):
            if (pub.parent / d).is_dir():
                src.setdefault(norm(d), f"Flutter folder {(pub.parent / d).relative_to(root).as_posix()}/")
    for app in [root / "app.json", *root.glob("*/app.json"), *root.glob("*/*/app.json")]:
        if not COMMON_SKIP.isdisjoint(app.parts) or not app.is_file():
            continue
        try:
            ex = (json.loads(app.read_text(encoding="utf-8", errors="replace")) or {}).get("expo")
        except (ValueError, OSError, AttributeError):
            continue
        if isinstance(ex, dict):
            for t in ex.get("platforms") or ["ios", "android"]:
                if norm(t):
                    src.setdefault(norm(t), f"{app.relative_to(root).as_posix()} expo.platforms")
    for cap in [*root.glob("capacitor.config.*"), *root.glob("*/capacitor.config.*"), *root.glob("*/*/capacitor.config.*")]:
        if cap.suffix not in (".ts", ".js", ".json") or not COMMON_SKIP.isdisjoint(cap.parts):
            continue
        where = cap.relative_to(root).as_posix()
        for d in ("android", "ios"):
            if (cap.parent / d).is_dir():
                src.setdefault(d, f"Capacitor {d}/ project next to {where}")
        if src:
            src.setdefault("web", f"Capacitor web build ({where})")
    if not src and langs & {"ts", "typescript"} and uses_react_native(root):
        for d in ("ios", "android"):
            src.setdefault(d, "React Native")
        for dep, t in (("react-native-web", "web"), ("react-native-windows", "windows"), ("react-native-macos", "macos")):
            if _has_dep(root, dep):
                src.setdefault(t, f"{dep} dependency")
    if not src and (_has_dep(root, "electron") or (root / "src-tauri" / "tauri.conf.json").is_file()):
        for d in DESKTOP:
            src.setdefault(d, "Electron / Tauri desktop app")
    if not src and marks and all(m.get("bridge") for m in marks):
        for m in marks:            # only native bridge modules are platform-specific: their platforms
            for p, x in m["cond"].values().items():
                if x is True:
                    src.setdefault(p, f"native bridge module {m['file']}")
    if not src and marks:
        for d in DESKTOP:
            src[d] = "desktop default (native code conditions)" if langs & {"rust", "c_cpp"} else "desktop default"
        for m in marks:
            v = m["cond"].values()
            only = [p for p, x in v.items() if x is True]
            if len(only) == 1 and only[0] not in src and _positive(m["cond"]):
                src[only[0]] = f"named by {m['cond'].text} @ {m['file']}:{m['line']}"
    order = [p for p in KNOWN if p in src]
    return order, {p: src[p] for p in order}


def _positive(c: "Cond") -> bool:
    """Does the condition name its target positively (`target_os = "android"`, `defined(__ANDROID__)`)? A fallback
    branch (`not(any(unix, windows))`, `#else`) is true on some exotic target without naming it."""
    if c.kind == "c":
        return "!" not in c.expr

    def walk(t):
        if not isinstance(t, tuple):
            return True
        if t[0] == "not":
            return False
        if t[0] in ("all", "any"):
            return all(walk(x) for x in t[1])
        return True
    return walk(c.expr)


def _has_dep(root: Path, name: str) -> bool:
    pj = root / "package.json"
    try:
        d = json.loads(pj.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return False
    return any(name in (d.get(k) or {}) for k in ("dependencies", "devDependencies"))


# ------------------------------------------------------------------ the index pass
def _local_name(n) -> str:
    nm = n.name or n.id.split(":", 1)[-1]
    for sep in ("#", "::", "."):
        nm = nm.rsplit(sep, 1)[-1]
    return nm.split("@", 1)[0]


def _inside(m: dict, line: int, name: str | None, lines_of) -> bool | None:
    """Is a reference / definition on `line` (to / named `name`) inside mark m? None when undecidable."""
    if line is None:
        return m["start"] <= 1 and m["end"] >= BIG
    if line < m["start"] or line > m["end"]:
        return False
    if m["scol"] is None or (m["start"] < line < m["end"]):
        return True
    txt = lines_of(m["file"], line)
    if txt is None or not name:
        return None
    occ = [x.start() for x in re.finditer(rf"(?<![\w$]){re.escape(name)}(?![\w$])", txt)]
    if not occ:
        return None
    ins = [(line > m["start"] or c >= m["scol"]) and (line < m["end"] or c < m["ecol"]) for c in occ]
    return True if all(ins) else (False if not any(ins) else None)


def apply(project, builder) -> dict:
    """Index pass: collect the platform conditions of every language, tag nodes and edges, link variant
    implementations, and find divergence. Returns the `platforms` index stats (empty dict: no platform-specific code)."""
    cfg = project.options.get("config") or {}
    pc = cfg.get("platforms") or {}
    root = Path(project.root)
    marks = list(builder.platform_marks)
    nodes_by_file: dict[str, list] = defaultdict(list)
    for n in builder.nodes.values():
        if n.file:
            nodes_by_file[n.file].append(n)
    langs = {lang for f, ns in nodes_by_file.items() for lang in (ns[0].lang,)}
    texts: dict[str, list[str] | None] = {}

    def lines_of(f: str, line: int):
        if f not in texts:
            try:
                texts[f] = (root / f).read_text(encoding="utf-8", errors="replace").split("\n")
            except OSError:
                texts[f] = None
        t = texts[f]
        return t[line - 1] if t is not None and 0 < line <= len(t) else None

    # source-text checks (Platform.OS, Platform.isX, kIsWeb, cfg!(...), process.platform)
    from .platform_scan import TRIGGERS, scan
    scanned = 0
    for f, ns in nodes_by_file.items():
        lang = {"ts": "ts", "dart": "dart", "rust": "rust"}.get(ns[0].lang or "")
        if not lang or f.endswith(".vue"):
            continue
        try:
            src = (root / f).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if not TRIGGERS[lang].search(src):
            continue
        texts[f] = src.split("\n")
        scanned += 1
        for sl, sc, el, ec, tree, text, at in scan(src, lang):
            mark(builder, f, sl, el, Cond("tree", tree, text), line=at, scol=sc, ecol=ec)
    marks = builder.platform_marks
    # .cg.yaml platforms.paths and the C / C++ directory / file-name conventions
    user_paths = pc.get("paths") or {}
    for f, ns in nodes_by_file.items():
        hit = next(((g, ts) for g, ts in user_paths.items() if fnmatch.fnmatch(f, g)), None)
        if hit:
            g, ts = hit
            tree = ("any", [_plat_atom(t) for t in ts]) if len(ts) > 1 else _plat_atom(ts[0])
            mark(builder, f, 1, BIG, Cond("tree", tree, f"{cfg.get('file', '.cg.yaml')} platforms.paths {g}"), line=1)
            continue
        if C_EXT.search(f) and pc.get("path_conventions", True) and ns[0].lang in ("c", "cpp"):
            pcv = path_convention(f)
            if pcv:
                mark(builder, f, 1, BIG, Cond("tree", _plat_atom(pcv[0]), pcv[1]), line=1)
    # React Native / Expo platform files
    groups: list[dict] = []
    if "ts" in langs and pc.get("file_suffixes", True) and uses_react_native(root):
        mods = {n.file for n in builder.nodes.values() if n.lang == "ts" and n.kind in ("module", "component") and n.file}
        fam: dict[tuple, dict[str, str]] = defaultdict(dict)
        for f in mods:
            d, _, base = f.rpartition("/")
            mm = RN_SUFFIX_RE.match(base)
            if mm:
                fam[(d, mm.group("stem"))][mm.group("plat")] = f
        for (d, stem), variants in fam.items():
            members = []
            conds = []
            for plat, f in sorted(variants.items()):
                c = Cond("tree", _plat_atom(plat), f".{plat} file")
                members.append((f, c))
                conds.append(_plat_atom(plat))
                mark(builder, f, 1, BIG, c, line=1)
            pre = f"{d}/{stem}" if d else stem
            bases = sorted(x for x in mods if re.fullmatch(re.escape(pre) + r"\.[cm]?[jt]sx?", x))
            for bf in bases[:1]:
                c = Cond("tree", ("not", ("any", conds)) if len(conds) > 1 else ("not", conds[0]),
                         f"base file next to {', '.join('.' + p for p in sorted(variants))}")
                members.append((bf, c))
                mark(builder, bf, 1, BIG, c, line=1)
            groups.append({"kind": "platform files", "name": pre, "members": members})
    # Dart conditional imports / exports
    imp_sites = [s for s in builder.platform_imports if "plain" not in s]
    if imp_sites:
        plain = defaultdict(int)
        for e in builder.edges.values():
            if e.kind == "IMPORTS" and not e.attrs.get("conditional"):
                plain[e.dst] += 1
        for s in builder.platform_imports:
            if "plain" in s:
                plain[f"module:{s['plain']}"] += 1
        per_file: dict[str, list] = defaultdict(list)
        seen_groups = {}
        for s in imp_sites:
            conds, members = [], []
            for name, val, f in s["configs"]:
                atom = _dart_atom(name, val)
                c = ("all", [("not", x) for x in conds] + [atom]) if conds else atom
                conds.append(atom)
                if f:
                    members.append((f, Cond("tree", c, f"if ({name}{'' if val in (None, 'true') else ' == ' + repr(val)})")))
            if s.get("default"):
                dc = ("all", [("not", x) for x in conds]) if len(conds) > 1 else ("not", conds[0])
                members.insert(0, (s["default"], Cond("tree", dc, "default of " + " / ".join(n for n, _, _ in s["configs"]))))
            for f, c in members:
                per_file[f].append((c, s))
            key = tuple(f for f, _ in members)
            if key not in seen_groups and len(members) > 1:
                seen_groups[key] = True
                groups.append({"kind": "conditional import", "name": s["default"] or members[0][0], "members": members,
                               "at": f"{s['file']}:{s['line']}"})
        for f, cs in per_file.items():
            if plain.get(f"module:{f}"):
                continue                                   # also imported unconditionally: available everywhere
            tree = cs[0][0].expr if len(cs) == 1 else ("any", [c.expr for c, _ in cs])
            mark(builder, f, 1, BIG, Cond("tree", tree, cs[0][0].text + f" ({cs[0][1]['file']}:{cs[0][1]['line']})"), line=1)
    marks = builder.platform_marks
    if not marks:
        return {}
    by_file = defaultdict(list)
    for m in marks:
        by_file[m["file"]].append(m)
    # 1. nodes defined under a condition
    ntag: dict[str, list] = defaultdict(list)
    for f, ms in by_file.items():
        for n in nodes_by_file.get(f, ()):
            for m in ms:
                if m["nodes"] and _inside(m, n.line, _local_name(n), lines_of):
                    ntag[n.id].append(m)
    nvals = {nid: _combine([m["cond"].values() for m in ms]) for nid, ms in ntag.items()}
    # 2. variant implementations: references to one variant reach every variant
    mirrored = _mirror(builder, groups, nvals)
    # 3. references made under a condition
    etag: dict[tuple, list] = {}
    efile = defaultdict(list)
    for k, e in builder.edges.items():
        if e.file in by_file:
            efile[e.file].append((k, e))
    for f, es in efile.items():
        ms = by_file[f]
        for k, e in es:
            dn = builder.nodes.get(e.dst)
            name = _local_name(dn) if dn is not None else e.dst.rsplit("#", 1)[-1].rsplit("::", 1)[-1]
            hit = [m for m in ms if _inside(m, e.line, name, lines_of)]
            if hit:
                etag[k] = hit
    _write_attrs(builder, ntag, nvals, etag)
    targets, tsrc = declared_targets(root, cfg, marks, set(langs) | ({"c_cpp"} if langs & {"c", "cpp"} else set()))
    unknown = [m for m in marks if any(m["cond"].values().get(p) is None for p in targets)]
    tagged_files = sorted({m["file"] for m in marks})
    st = {"targets": targets, "target_sources": tsrc, "conditions": len(marks), "files_with_conditions": len(tagged_files),
          "tagged_nodes": len(ntag), "tagged_edges": len(etag), "variant_edges": mirrored, "text_scanned_files": scanned,
          "unevaluated_conditions": len(unknown),
          "unevaluated_per_target": {p: sum(1 for m in unknown if m["cond"].values().get(p) is None) for p in targets},
          "unevaluated_samples": sorted({f"{m['cond'].text} @ {m['file']}:{m['line']}" for m in unknown})[:8]}
    st["per_target"] = _per_target(builder, nodes_by_file, nvals, targets)
    st["divergence"] = divergence_findings(builder, groups, nvals, targets)
    return st


def _dart_atom(name: str, val):
    mm = re.fullmatch(r"dart\.library\.(\w+)", name or "")
    if mm and val in (None, "true"):
        return ("atom", "dart_library", mm.group(1))
    if mm and val == "false":
        return ("not", ("atom", "dart_library", mm.group(1)))
    return ("atom", "?", name)


def _slot(n, f: str):
    """Position of a node inside its file, so the same symbol can be found in a sibling variant file."""
    kind, key = n.id.split(":", 1)
    if key == f:
        return (kind, "")
    if key.startswith(f + "#"):
        return (kind, key[len(f) + 1:])
    return None


def _mirror(builder, groups: list[dict], nvals: dict) -> int:
    """Edges into one member of a variant group -> the same edge into the matching symbol of every other member."""
    adds = []
    slot_maps = []
    for g in groups:
        files = [f for f, _ in g["members"]]
        fset = set(files)
        by_slot: dict[tuple, dict[str, str]] = defaultdict(dict)
        for n in builder.nodes.values():
            if n.file in fset:
                s = _slot(n, n.file)
                if s is not None:
                    by_slot[s][n.file] = n.id
        idx = {nid: (s, f) for s, fm in by_slot.items() for f, nid in fm.items()}
        slot_maps.append((fset, by_slot, idx))
    # Rust / C / C++: alternative definitions of one symbol (`key` + `key@line` / `key@file:line`) on different targets
    alts = _alt_groups(builder, nvals)
    alt_of = {}
    for base, ids in alts.items():
        if len(ids) < 2:
            continue
        sets = {tuple(sorted(p for p, v in nvals.get(i, {p: True for p in KNOWN}).items() if v is not False)) for i in ids}
        if len(sets) < 2:
            continue                       # same targets: overloads / macro repeats, not platform variants
        for i in ids:
            alt_of[i] = ids
    for k, e in list(builder.edges.items()):
        if e.kind in ("CONTAINS", "DEFINES", "GATED_BY"):
            continue
        for fset, by_slot, idx in slot_maps:
            hit = idx.get(e.dst)
            if hit is None:
                continue
            sn = builder.nodes.get(e.src)
            if sn is not None and sn.file in fset:
                continue
            s, f = hit
            for f2, nid in by_slot[s].items():
                if f2 != f:
                    adds.append((e, nid))
        if e.dst in alt_of and e.src not in alt_of[e.dst]:
            for nid in alt_of[e.dst]:
                if nid != e.dst:
                    adds.append((e, nid))
    n0 = len(builder.edges)
    for e, nid in adds:
        builder.add_edge(e.src, nid, e.kind, e.file, e.line, e.confidence, **{**e.attrs, "platform_variant_of": e.dst})
    return len(builder.edges) - n0


def _write_attrs(builder, ntag, nvals, etag) -> None:
    def put(attrs: dict, ms: list, vals: dict):
        attrs["platforms"] = [p for p in KNOWN if vals[p] is not False]
        unk = [p for p in KNOWN if vals[p] is None]
        if unk:
            attrs["platform_unknown"] = unk
        texts = list(dict.fromkeys(m["cond"].text for m in ms))
        attrs["platform_expr"] = " && ".join(texts)[:240]
        attrs["platform_at"] = f"{ms[0]['file']}:{ms[0]['line']}"
    for nid, ms in ntag.items():
        n = builder.nodes.get(nid)
        if n is not None:
            if n.attrs is None:
                n.attrs = {}
            put(n.attrs, ms, nvals[nid])
    for k, ms in etag.items():
        e = builder.edges.get(k)
        if e is not None:
            e.attrs = dict(e.attrs or {})
            put(e.attrs, ms, _combine([m["cond"].values() for m in ms]))


CODE = ("function", "method", "class", "struct", "enum", "trait", "union", "typedef", "type_alias", "component",
        "composable", "store", "macro")


def _per_target(builder, nodes_by_file, nvals, targets) -> dict:
    out = {}
    files = {f for f, ns in nodes_by_file.items() if any(n.kind in CODE for n in ns)}
    for p in targets:
        syms = sum(1 for n in builder.nodes.values() if n.kind in CODE and nvals.get(n.id, {}).get(p, True) is not False)
        only = sum(1 for nid, v in nvals.items() if v.get(p) is not False and builder.nodes.get(nid) is not None
                   and builder.nodes[nid].kind in CODE)
        fl = sum(1 for f in files if not all(nvals.get(n.id, {}).get(p, True) is False for n in nodes_by_file[f]
                                              if n.kind in CODE))
        out[p] = {"files": fl, "symbols": syms, "platform_specific_symbols": only}
    return out


def _avail(v: dict | None, p: str) -> bool:
    return v is None or v.get(p) is not False


def _vkey(nid: str, n, nvals: dict | None = None) -> str:
    """Variant key of a Rust / C / C++ definition: its id without the `@file:line` suffix. In C / C++ a function
    and a function-like macro of the same name are variants too (`#ifdef _WIN32 #define f(h) ... #else` + `void f`).
    In Rust, items of sibling modules gated per platform are variants (`#[cfg(unix)] mod unix; #[cfg(windows)] mod
    windows;` each with `fn new`, re-exported by the parent): the gated module segment is replaced by `*`."""
    b = nid.split("@", 1)[0]
    if n is None:
        return b
    if n.lang in ("c", "cpp") and n.kind in ("function", "macro") and n.name:
        return f"c-fn:{n.file}#{n.name}" if "#" in b else f"c-fn:{n.name}"     # `#`: a static, file-local function
    if n.lang == "rust" and nvals and ":" in b:
        kind, path = b.split(":", 1)
        parts = path.split("::")
        for i in range(len(parts) - 1, 1, -1):
            if f"mod:{'::'.join(parts[:i])}" in nvals:
                return f"{kind}:{'::'.join(parts[:i - 1])}::*::{'::'.join(parts[i:])}"
    return b


ALT_KINDS = ("function", "method", "struct", "enum", "type_alias", "typedef", "class", "union", "macro", "const",
             "static", "global")


def _alt_groups(builder, nvals: dict) -> dict[str, list[str]]:
    """Rust / C / C++ alternative definitions of one symbol, keyed by _vkey."""
    alts: dict[str, list[str]] = defaultdict(list)
    for nid, n in builder.nodes.items():
        if n.lang in ("rust", "c", "cpp") and n.kind in ALT_KINDS:
            alts[_vkey(nid, n, nvals)].append(nid)
    # a static C function and a macro / function of the same name visible in its file are variants too
    for k in [k for k in alts if k.startswith("c-fn:") and "#" in k]:
        file, name = k[5:].rsplit("#", 1)
        g = f"c-fn:{name}"
        if g in alts and any(builder.nodes[i].file == file or builder.nodes[i].kind == "macro" for i in alts[g]):
            alts[g] += alts.pop(k)
    return alts


def _public(n, group_files: set, ext_refs: dict, users: set) -> bool:
    """Part of the API the importers of a variant group rely on: used by a file that imports the group, or, when the
    group has no importer in the graph, exported (TS) / public (Dart) / used from outside the group."""
    refs = ext_refs.get(n.id, set())
    if users:
        return bool(refs & users)
    if (n.attrs or {}).get("exported"):
        return True
    if n.lang == "dart" and not _local_name(n).startswith("_"):
        return True
    return bool(refs - group_files)


def divergence_findings(builder, groups: list[dict], nvals: dict, targets: list[str], limit: int = 400) -> dict:
    """Platform divergence for the declared targets:
      variants      a symbol / module implemented per platform (variant groups) and the targets none covers
      api_surface   symbols one variant file defines and a sibling variant does not
      missing_callee  references live on a target where the referenced code does not exist there (and no variant does)
    """
    tset = [p for p in KNOWN if p in targets]
    out = {"variants": [], "api_surface": [], "missing_callee": []}
    importers = defaultdict(set)
    ext_refs = defaultdict(set)          # node -> files referring to it
    for e in builder.edges.values():
        if e.kind in ("IMPORTS", "CALLS", "REFERENCES_FN") and e.file:
            importers[e.dst].add(f"{e.file}:{e.line}")
        if e.kind not in ("CONTAINS", "DEFINES") and e.file:
            ext_refs[e.dst].add(e.file)

    def importers_of(g) -> set:
        return {u for f, _ in g["members"] for u in importers.get(f"module:{f}", ())}
    for g in groups:
        covered = set()
        mem = []
        for f, c in g["members"]:
            ps = [p for p, v in c.values().items() if v is not False]
            covered |= set(ps)
            mem.append({"file": f, "platforms": ps, "condition": c.text})
        used = sorted({u for f, _ in g["members"] for u in importers.get(f"module:{f}", ())
                       if not any(u.startswith(m + ":") for m, _ in g["members"])})
        missing = [p for p in tset if p not in covered]
        out["variants"].append({"kind": g["kind"], "name": g["name"], "members": mem, "covered": [p for p in KNOWN if p in covered and p in tset],
                                "missing": missing, "used_at": used[:5], **({"at": g["at"]} if g.get("at") else {})})
        # API surface: public symbols a member defines that a sibling lacks (exported TS symbols, public Dart names,
        # anything referenced from outside the group)
        files = {f for f, _ in g["members"]}
        users = {u.rsplit(":", 1)[0] for u in importers_of(g)} - files     # files that import the group
        defs = {}
        for f, c in g["members"]:
            defs[f] = {(_slot(n, f) or ("?", n.id))[1]: n for n in builder.nodes.values() if n.file == f
                       and n.kind in ("function", "method", "class", "component", "composable", "store") and _slot(n, f)
                       and _public(n, files, ext_refs, users)}
        allnames = set().union(*[set(d) for d in defs.values()]) if defs else set()
        for nm in sorted(allnames):
            have = [f for f in defs if nm in defs[f]]
            lack = [f for f in defs if nm not in defs[f]]
            if have and lack and "#" not in nm and "." not in nm:
                lack_pl = sorted({p for f, c in g["members"] if f in lack for p, v in c.values().items() if v is not False and p in tset})
                if lack_pl:
                    out["api_surface"].append({"group": g["name"], "symbol": nm, "defined_in": have, "missing_in": lack,
                                               "missing_on": [p for p in KNOWN if p in lack_pl]})
    # id-variant groups (Rust / C): covered targets per symbol
    alts = {k: ids for k, ids in _alt_groups(builder, nvals).items() if any(i in nvals for i in ids)}
    vkey_of = {i: k for k, ids in alts.items() for i in ids}
    for base, ids in sorted(alts.items()):
        if len(ids) < 2 or not any(builder.nodes[i].kind in ("function", "method", "macro") for i in ids):
            continue
        sets = {tuple(p for p in KNOWN if _avail(nvals.get(i), p)) for i in ids}
        if len(sets) < 2:
            continue                       # same targets: overloads / repeated declarations, not platform variants
        covered = {p for i in ids for p in KNOWN if _avail(nvals.get(i), p)}
        n0 = builder.nodes[ids[0]]
        out["variants"].append({"kind": "per-platform definitions", "name": base.split(":", 1)[1] if "::*::" in base else (n0.fqn or n0.name),
                                "members": [{"id": i, "file": builder.nodes[i].file, "line": builder.nodes[i].line,
                                             "platforms": [p for p in KNOWN if _avail(nvals.get(i), p)],
                                             "condition": builder.nodes[i].attrs.get("platform_expr")} for i in ids],
                                "covered": [p for p in KNOWN if p in covered and p in tset], "missing": [p for p in tset if p not in covered],
                                "used_at": sorted(importers.get(ids[0], ()))[:5]})
    # references on a target where the callee does not exist (a reference to any variant counts for the group)
    gslot = {}
    for g in groups:
        files = {f for f, _ in g["members"]}
        for n in builder.nodes.values():
            if n.file in files:
                sl = _slot(n, n.file)
                if sl is not None:
                    gslot[n.id] = ("group", g["name"], sl)
    sites = defaultdict(list)
    for e in builder.edges.values():
        if e.kind in ("CALLS", "REFERENCES_FN", "USES_TYPE", "USES_VALUE", "IMPORTS", "INSTANTIATES", "RENDERS",
                      "USES_COMPOSABLE", "USES_STORE") and (e.dst in nvals or e.attrs.get("platform_variant_of") in nvals):
            if e.kind == "USES_VALUE" and (e.confidence == "heuristic" or e.dst.startswith("macro:")):
                # a global matched by name only, or a macro tested by #ifdef: too weak to call a target broken
                continue
            base = e.attrs.get("platform_variant_of") or e.dst
            dn = builder.nodes.get(base)
            if base in gslot:
                base = gslot[base]
            elif dn is not None and dn.lang in ("rust", "c", "cpp"):
                base = vkey_of.get(base, base)
            sites[(e.src, e.file, e.line, e.kind, base)].append(e)
    # every module one import line can resolve to (a conditional import names several on one line)
    import_alts = defaultdict(set)
    outside_default = set()           # conditional import whose default is outside the index (e.g. `dart:isolate`)
    for e in builder.edges.values():
        if e.kind == "IMPORTS":
            import_alts[(e.src, e.file, e.line)].add(e.dst)
            if e.attrs.get("conditional"):
                outside_default.add((e.src, e.file, e.line))
    for e in builder.edges.values():
        if e.kind == "IMPORTS" and e.attrs.get("conditional") and not e.attrs.get("condition"):
            outside_default.discard((e.src, e.file, e.line))
    for (src, f, line, kind, base), es in sites.items():
        sv = nvals.get(src)
        ev = es[0].attrs
        live = [p for p in tset if _avail(sv, p) and (not ev.get("platforms") or p in ev["platforms"])]
        have = {p for e in es for p in KNOWN if _avail(nvals.get(e.dst), p)}
        if kind == "IMPORTS":
            have |= {p for d in import_alts.get((src, f, line), ()) for p in KNOWN if _avail(nvals.get(d), p)}
            if (src, f, line) in outside_default:
                have |= set(KNOWN)
        if isinstance(base, str) and base in alts:        # any definition of the symbol on that target will do
            have |= {p for i in alts[base] for p in KNOWN if _avail(nvals.get(i), p)}
        miss = [p for p in live if p not in have]
        if miss:
            dst = es[0].attrs.get("platform_variant_of") or es[0].dst
            out["missing_callee"].append({"from": src, "to": dst, "kind": kind, "at": f"{f}:{line}", "missing_on": miss,
                                          "callee_platforms": [p for p in KNOWN if p in have],
                                          "callee_condition": " | ".join(dict.fromkeys(
                                              (builder.nodes[e.dst].attrs or {}).get("platform_expr") or "" for e in es
                                              if e.dst in builder.nodes)) or None})
    out["variants"] = [v for v in out["variants"]]
    out["counts"] = {k: len(v) for k, v in out.items()}
    for k in ("variants", "api_surface", "missing_callee"):
        out[k] = out[k][:limit]
    return out


# ------------------------------------------------------------------ queries
def has_platforms(st) -> bool:
    return bool((st.meta().get("stats") or {}).get("platforms"))


def summary(st) -> dict:
    return (st.meta().get("stats") or {}).get("platforms") or {}


def exclusions(st, platform: str) -> dict:
    """Nodes and edges that do not exist on `platform` (their condition is false there), and how many conditions
    could not be evaluated for it (kept live). Cached per store; also fills temp.t_px with the excluded edge ids."""
    cache = getattr(st, "_platform_x", None)
    if cache is None:
        cache = st._platform_x = {}
    if platform in cache:
        return cache[platform]
    nodes, unknown_n = set(), 0
    for r in st.q("SELECT id, attrs FROM nodes WHERE attrs LIKE '%\"platforms\"%'"):
        a = json.loads(r["attrs"] or "{}")
        if "platforms" in a:
            if platform not in a["platforms"]:
                nodes.add(r["id"])
            elif platform in (a.get("platform_unknown") or ()):
                unknown_n += 1
    edges, unknown_e = set(), 0
    for r in st.q("SELECT id, attrs FROM edges WHERE attrs LIKE '%\"platforms\"%'"):
        a = json.loads(r["attrs"] or "{}")
        if "platforms" in a:
            if platform not in a["platforms"]:
                edges.add(r["id"])
            elif platform in (a.get("platform_unknown") or ()):
                unknown_e += 1
    if nodes:
        st.db.execute("DROP TABLE IF EXISTS temp.t_pxn")
        st.db.execute("CREATE TEMP TABLE t_pxn(id TEXT PRIMARY KEY)")
        st.db.executemany("INSERT OR IGNORE INTO t_pxn VALUES (?)", [(x,) for x in nodes])
        edges |= {r["id"] for r in st.q("SELECT e.id FROM edges e JOIN t_pxn n ON n.id = e.src")}
        edges |= {r["id"] for r in st.q("SELECT e.id FROM edges e JOIN t_pxn n ON n.id = e.dst")}
    res = {"platform": platform, "nodes": nodes, "edges": edges, "unknown": unknown_n + unknown_e}
    cache[platform] = res
    return res


def temp_table(st, platform: str) -> str:
    """Name of a temp table holding the edge ids excluded on `platform` (for SQL traversals)."""
    x = exclusions(st, platform)
    name = f"t_px_{platform}"
    if not getattr(st, "_platform_tbl", None):
        st._platform_tbl = set()
    if name not in st._platform_tbl:
        st.db.execute(f"DROP TABLE IF EXISTS temp.{name}")
        st.db.execute(f"CREATE TEMP TABLE {name}(id INTEGER PRIMARY KEY)")
        st.db.executemany(f"INSERT OR IGNORE INTO {name} VALUES (?)", [(i,) for i in x["edges"]])
        st._platform_tbl.add(name)
    return name


def filter_info(st, platform: str | None) -> dict | None:
    """What a --platform filter did, for the reply: the target, excluded nodes / edges, unevaluated conditions."""
    if not platform:
        return None
    x = exclusions(st, platform)
    s = summary(st)
    upt = s.get("unevaluated_per_target") or {}
    info = {"platform": platform, "excluded_nodes": len(x["nodes"]), "excluded_edges": len(x["edges"]),
            # conditions per declared target; for another target, the tagged code whose condition is unknown there
            "unevaluated_conditions": upt[platform] if platform in upt else x["unknown"],
            "kept_unevaluated": x["unknown"]}
    if not s:
        info["note"] = "this graph has no platform-specific code: the filter changes nothing"
    elif s.get("targets") and platform not in s["targets"]:
        info["note"] = f"{platform} is not a declared target of this project ({', '.join(s['targets'])})"
    return info


def render_filter(info: dict | None) -> str:
    if not info:
        return ""
    s = (f"platform: {info['platform']} ({info['excluded_nodes']} nodes and {info['excluded_edges']} references not built "
         f"for it left out; {info['unevaluated_conditions']} conditions could not be evaluated for it, the code under "
         f"them stays in)")
    return s + (f"; {info['note']}" if info.get("note") else "")


def node_platforms(attrs_json: str | None) -> dict:
    if not attrs_json or '"platforms"' not in attrs_json:
        return {}
    try:
        a = json.loads(attrs_json) or {}
    except ValueError:
        return {}
    out = {}
    if "platforms" in a:
        out["platforms"] = a["platforms"]
        if a.get("platform_unknown"):
            out["platform_unknown"] = a["platform_unknown"]
        if a.get("platform_expr"):
            out["platform_expr"] = a["platform_expr"]
    return out


def label(n: dict) -> str:
    """'  [ios, android]' for platform-specific code, '' for code on every target."""
    ps = n.get("platforms")
    if ps is None:
        return ""
    unk = n.get("platform_unknown") or []
    return "  [" + (", ".join(p + ("?" if p in unk else "") for p in ps) or "no known target") + "]"


def divergence(st, kind: str | None = None, target: str | None = None) -> dict:
    s = summary(st)
    d = dict(s.get("divergence") or {})
    if target:
        d["variants"] = [v for v in d.get("variants", []) if target in v.get("missing", [])]
        d["api_surface"] = [v for v in d.get("api_surface", []) if target in v.get("missing_on", [])]
        d["missing_callee"] = [v for v in d.get("missing_callee", []) if target in v.get("missing_on", [])]
    if kind:
        d = {k: (v if k == kind else []) for k, v in d.items() if k != "counts"} | {"counts": d.get("counts")}
    return {"targets": s.get("targets") or [], "target_sources": s.get("target_sources") or {}, **d}


def render_divergence(res: dict, limit: int = 40) -> str:
    if not res.get("targets") and not res.get("variants"):
        return "no platform-specific code in this graph (no platform conditions, platform files or conditional imports)"
    out = [f"targets: {', '.join(res['targets']) or '(none declared)'}"
           + (f"  ({'; '.join(f'{k}: {v}' for k, v in list(res.get('target_sources', {}).items())[:4])})" if res.get("target_sources") else "")]
    var = res.get("variants") or []
    gaps = [v for v in var if v.get("missing")]
    out.append(f"\n== VARIANTS (implemented per platform): {len(var)}, {len(gaps)} with a declared target no variant covers")
    for v in sorted(var, key=lambda v: (not v.get("missing"), v["name"]))[:limit]:
        miss = f"   missing: {', '.join(v['missing'])}" if v.get("missing") else ""
        out.append(f"  {v['name']}  [{v['kind']}]  covered: {', '.join(v['covered']) or '-'}{miss}")
        for m in v["members"][:6]:
            loc = m.get("file", "") + (f":{m['line']}" if m.get("line") else "")
            out.append(f"      {loc}  {', '.join(m['platforms']) or 'no known target'}  ({m.get('condition') or '-'})")
        if v.get("used_at"):
            out.append(f"      used at: {', '.join(v['used_at'][:3])}")
    api = res.get("api_surface") or []
    if api:
        out.append(f"\n== API SURFACE DIFFERS (a variant lacks a symbol its siblings define): {len(api)}")
        for a in api[:limit]:
            out.append(f"  {a['symbol']}  in {', '.join(a['defined_in'])}; missing in {', '.join(a['missing_in'])} "
                       f"(targets {', '.join(a['missing_on'])})")
    mc = res.get("missing_callee") or []
    if mc:
        out.append(f"\n== REFERENCED WHERE THE CALLEE IS NOT BUILT: {len(mc)}")
        for m in mc[:limit]:
            out.append(f"  {m['from']} -{m['kind']}-> {m['to']}  @ {m['at']}  missing on: {', '.join(m['missing_on'])}"
                       f"  (callee: {', '.join(m['callee_platforms']) or 'no known target'}"
                       + (f", {m['callee_condition']}" if m.get("callee_condition") else "") + ")")
    c = res.get("counts") or {}
    if any(c.get(k, 0) > len(res.get(k) or []) for k in ("variants", "api_surface", "missing_callee")):
        out.append(f"\n(lists capped; totals: {', '.join(f'{k} {v}' for k, v in c.items())})")
    return "\n".join(out)


def render_summary(s: dict) -> str:
    if not s:
        return "no platform-specific code in this graph"
    out = [f"targets: {', '.join(s.get('targets') or []) or '(none)'}"]
    for p, src in (s.get("target_sources") or {}).items():
        out.append(f"  {p}: {src}")
    out.append(f"conditions: {s['conditions']} in {s['files_with_conditions']} files; tagged {s['tagged_nodes']} symbols and "
               f"{s['tagged_edges']} references; {s.get('variant_edges', 0)} variant links")
    pt = s.get("per_target") or {}
    for p in s.get("targets") or []:
        if p in pt:
            x = pt[p]
            out.append(f"  {p:8} {x['files']} files, {x['symbols']} symbols ({x['platform_specific_symbols']} platform-specific)")
    if s.get("unevaluated_conditions"):
        out.append(f"not evaluated: {s['unevaluated_conditions']} conditions (kept live), e.g. "
                   + "; ".join(s.get("unevaluated_samples") or [])[:300])
    d = (s.get("divergence") or {}).get("counts") or {}
    if d:
        out.append(f"divergence: {d.get('variants', 0)} variant groups, {d.get('api_surface', 0)} API differences, "
                   f"{d.get('missing_callee', 0)} references to code missing on a target (cg platforms divergence)")
    return "\n".join(out)
