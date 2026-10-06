"""`cg hooks` and `cg refresh` (issue #145, part 1).

`cg hooks` is opt-in: it writes one marked block into the git hooks `post-commit`, `post-checkout`
and `post-merge`, found with `git rev-parse --git-path hooks` (so `core.hooksPath` and worktrees
apply). Nothing is installed by default. Bytes outside the block stay; uninstall restores them.
A file cg created that is only the shebang afterwards is deleted.

`cg refresh` is what the hooks run, detached. It never indexes onto the live database: the new
graph is built at `<db>.refresh.tmp` (distinct from the MCP server's `<db>.tmp`) and moved into
place with `os.replace` after a non-empty result. A workspace `.cg.yaml` `apps:` list is indexed
on that same temporary path; per-app databases (derived from the db path) are moved beside the
real db. Concurrent refreshes share `<db>.refresh.lock` (non-blocking `flock` on POSIX,
`msvcrt.locking` on Windows; both are released when the process dies). Every run touches
`<db>.refresh.pending` before it tries the lock; the loser exits 0, and the holder keeps running
passes while that flag is set (at most three passes per run), so no request is lost.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

try:
    import fcntl
except ImportError:          # Windows
    fcntl = None
try:
    import msvcrt
except ImportError:          # POSIX
    msvcrt = None

HOOKS = ("post-commit", "post-checkout", "post-merge")
BEGIN = "# >>> cg hooks (managed by cg hooks; edit with cg, not here) >>>"
END = "# <<< cg hooks <<<"
_BLOCK = re.compile(re.escape(BEGIN) + r".*?" + re.escape(END) + r"\n?", re.S)
_SHELLS = {"sh", "bash", "dash", "ksh", "zsh", "ash"}
_SHEBANG_ONLY = "#!/bin/sh"
_OLD_CLI = "-m " + "codegraph" + ".cli"


def _sh_quote(text: str) -> str:
    return "'" + text.replace("'", "'\\''") + "'"


def _read(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_bytes().decode("utf-8", "surrogateescape")


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode("utf-8", "surrogateescape"))


def _shell_hook(text: str) -> bool:
    """True when the file starts with a shell shebang (`sh`, `bash`, …), including `env sh`."""
    if not text.startswith("#!"):
        return False
    line = text.split("\n", 1)[0][2:].strip()
    parts = line.split()
    if not parts:
        return False
    interp = Path(parts[0]).name
    if interp == "env":
        parts = parts[1:]
        while parts and parts[0].startswith("-"):
            parts = parts[1:]
        interp = Path(parts[0]).name if parts else ""
    return interp in _SHELLS


def _block_text(hook: str, root: str, db: str, py: str) -> str:
    """POSIX sh. Never calls `exit`. The last command inside the guard is `true`, so a user's
    `set -e` hook (and git) keeps going. Output of the refresh is discarded and the process is
    backgrounded."""
    qroot, qdb, qpy = _sh_quote(root), _sh_quote(db), _sh_quote(py)
    cg_name = "cg.exe" if os.name == "nt" else "cg"
    qcg = _sh_quote(str(Path(py).parent / cg_name))
    ind = "    " if hook == "post-checkout" else "  "
    # The `cg` next to the Python that ran `cg hooks install` first, then that Python, then `cg` on PATH.
    # A different (older) `cg` earlier on PATH, e.g. a global tool install next to a project venv, may not
    # have `refresh` at all.
    body = [
        f"{ind}if [ -f {qcg} ]; then",
        f"{ind}  ( {qcg} refresh {qroot} --db {qdb} --quiet </dev/null >/dev/null 2>&1 & ) >/dev/null 2>&1",
        f"{ind}elif [ -f {qpy} ]; then",
        f"{ind}  ( {qpy} -m cg_code_graph.cli refresh {qroot} --db {qdb} --quiet </dev/null >/dev/null 2>&1 & ) >/dev/null 2>&1",
        f"{ind}elif command -v cg >/dev/null 2>&1; then",
        f"{ind}  ( cg refresh {qroot} --db {qdb} --quiet </dev/null >/dev/null 2>&1 & ) >/dev/null 2>&1",
        f"{ind}fi",
    ]
    lines = [
        BEGIN,
        f"# cg-root: {root}",
        f"# cg-db: {db}",
        f"# cg-hook: {hook}",
        'if [ "${CG_NO_HOOKS:-${CODEGRAPH_NO_HOOKS:-}}" != 1 ]; then',
    ]
    if hook == "post-checkout":
        lines.append('  if [ "$1" != "$2" ]; then')
        lines.extend(body)
        lines.append("  fi")
    else:
        lines.extend(body)
    lines.extend(["  true", "fi", END])
    return "\n".join(lines) + "\n"


def _insert(old: str, block: str) -> str:
    if not old:
        return "#!/bin/sh\n" + block
    nl = old.find("\n")
    if nl == -1:
        return old + "\n" + block
    return old[:nl + 1] + block + old[nl + 1:]


def _meta(text: str) -> tuple[str | None, str | None]:
    root = db = None
    for line in text.splitlines():
        if line.startswith("# cg-root: "):
            root = line[len("# cg-root: "):]
        elif line.startswith("# cg-db: "):
            db = line[len("# cg-db: "):]
    return root, db


def _hooks_dir(root: Path) -> Path | None:
    try:
        proc = subprocess.run(["git", "rev-parse", "--git-path", "hooks"], cwd=root,
                              capture_output=True, text=True)
    except OSError:          # git not installed, or root is not a directory
        return None
    if proc.returncode != 0:
        return None
    raw = (proc.stdout or "").strip()
    if not raw:
        return None
    path = Path(raw)
    if not path.is_absolute():
        path = (root / path).resolve()
    return path


def _shared_hooks_dir(root: Path, hooks: Path) -> bool:
    """True when `hooks` is not inside the repository's common git directory (a shared core.hooksPath)."""
    try:
        proc = subprocess.run(["git", "rev-parse", "--path-format=absolute", "--git-common-dir", "--show-toplevel"], cwd=root,
                              capture_output=True, text=True)
    except OSError:
        return False
    lines = (proc.stdout or "").splitlines()
    if proc.returncode != 0 or not lines:
        return False
    for base in lines:       # common git dir, then the work tree top (`.githooks` in the checkout is per-repo)
        try:
            hooks.resolve().relative_to(Path(base).resolve())
            return False
        except ValueError:
            continue
    return True


def plan_hook(path: Path, hook: str, remove: bool, root: str | None, db: str | None,
              py: str) -> dict:
    """One hook file. action: insert | update | remove | noop | skip.
    `skip` is a non-shell hook, left byte-for-byte unchanged."""
    old = _read(path)
    if old and not _shell_hook(old):
        return {"path": str(path), "hook": hook, "action": "skip", "old": old, "new": old}
    if remove:
        if not _BLOCK.search(old):
            return {"path": str(path), "hook": hook, "action": "noop", "old": old, "new": old}
        new = _BLOCK.sub("", old, count=1)
        return {"path": str(path), "hook": hook, "action": "remove", "old": old, "new": new}
    block = _block_text(hook, root or "", db or "", py)
    found = _BLOCK.search(old)
    if found:
        new = old[:found.start()] + block + old[found.end():]
        action = "noop" if new == old else "update"
        return {"path": str(path), "hook": hook, "action": action, "old": old, "new": new}
    new = _insert(old, block)
    return {"path": str(path), "hook": hook, "action": "insert", "old": old, "new": new}


def _preview(plan: dict) -> str:
    if plan["action"] == "skip":
        return f"{plan['path']}: skipped (not a shell hook)"
    if plan["action"] == "noop":
        if not plan["old"]:
            return f"{plan['path']}: no change (not installed)"
        return f"{plan['path']}: no change"
    import difflib
    diff = difflib.unified_diff(plan["old"].splitlines(True), plan["new"].splitlines(True),
                                fromfile=plan["path"], tofile=plan["path"], n=1)
    body = "".join(diff).rstrip("\n")
    return f"{plan['path']}  [{plan['action']}]\n{body}"


def _status_line(path: Path, hook: str) -> str:
    text = _read(path)
    if text and not _shell_hook(text):
        return f"{hook}: skipped (not a shell hook)"
    found = _BLOCK.search(text)
    if not found:
        return f"{hook}: not installed"
    if _OLD_CLI in found.group(0):
        return f"outdated hook {hook}: re-run cg hooks install"
    root, db = _meta(text)
    extra = ""
    if root:
        extra += f" root={root}"
    if db:
        extra += f" db={db}"
    return f"{hook}: installed{extra}"


def run(action: str, root: str = ".", db: str | None = None, dry_run: bool = False,
        assume_yes: bool = False, interpreter: str | None = None, input_fn=input, out=print) -> int:
    """`install`, `uninstall` or `status`. Returns a process exit code.
    `interpreter` is the Python recorded in the block (default: this interpreter)."""
    rootp = Path(root).resolve()
    hooks = _hooks_dir(rootp)
    if hooks is None:
        out(f"cg hooks: {rootp} is not a git repository")
        return 2
    if action == "status":
        for hook in HOOKS:
            out(_status_line(hooks / hook, hook))
        return 0
    if action == "install" and not db:
        out("cg hooks install: --db is required")
        return 2
    db_abs = str(Path(db).resolve()) if db else None
    py = interpreter or sys.executable
    remove = action == "uninstall"
    if not remove and any(c in v for v in (str(rootp), db_abs or "", py) for c in "\r\n"):
        out("cg hooks install: the project root, --db and interpreter paths must not contain a line break")
        return 2
    if not remove and _shared_hooks_dir(rootp, hooks):
        out(f"note: {hooks} is outside this repository's git directory (core.hooksPath); other "
            f"repositories using it will also run this block")
    plans = [plan_hook(hooks / hook, hook, remove, str(rootp), db_abs, py) for hook in HOOKS]
    for plan in plans:
        out(_preview(plan))
    changing = [p for p in plans if p["action"] in ("insert", "update", "remove")]
    if dry_run:
        out("(dry run: nothing written)")
        return 0
    if not changing:
        out("nothing to do")
        return 0
    if not assume_yes:
        resp = input_fn(f"apply {len(changing)} change(s)? [y/N] ")
        if (resp or "").strip().lower() not in ("y", "yes"):
            out("aborted; nothing written")
            return 1
    for plan in changing:
        path = Path(plan["path"])
        new = plan["new"]
        if plan["action"] == "remove" and new.strip() == _SHEBANG_ONLY:
            if path.exists():
                path.unlink()
            out(f"removed {plan['path']} (only the shebang cg wrote was left)")
            continue
        existed = path.exists()
        mode = path.stat().st_mode if existed else None
        _write(path, new)
        if mode is None:
            os.chmod(path, 0o755)
        else:
            os.chmod(path, mode | 0o111)
        out(f"wrote {plan['path']} ({plan['action']})")
    return 0


def outdated_names(root: str | Path) -> list[str]:
    """Hook names whose cg block still runs the removed `codegraph.cli` module."""
    hooks = _hooks_dir(Path(root))
    if hooks is None:
        return []
    out = []
    for hook in HOOKS:
        found = _BLOCK.search(_read(hooks / hook))
        if found and _OLD_CLI in found.group(0):
            out.append(hook)
    return out


def _git_ok(root: Path) -> bool:
    proc = subprocess.run(["git", "-C", str(root), "rev-parse", "--is-inside-work-tree"],
                          capture_output=True, text=True)
    return proc.returncode == 0 and (proc.stdout or "").strip() == "true"


def _listed_files(root: Path) -> list[str] | None:
    """Paths from `git ls-files -z -c -o --exclude-standard`, relative to root. None if git fails."""
    proc = subprocess.run(["git", "-C", str(root), "ls-files", "-z", "-c", "-o", "--exclude-standard"],
                          capture_output=True)
    if proc.returncode != 0:
        return None
    out = []
    for part in proc.stdout.split(b"\0"):
        if part:
            out.append(part.decode("utf-8", "surrogateescape"))
    return out


def _db_artifact(root: Path, rel: str, db: str | None) -> bool:
    """True for the graph DB and its sidecars (`<db>`, `<db>.*`, `<db>-*`), so a DB inside ROOT
    does not change the fingerprint every time refresh rewrites it."""
    if not db:
        return False
    skip = str(Path(db).resolve())
    try:
        abs_p = str((root / rel).resolve())
    except OSError:
        abs_p = str(root / rel)
    return abs_p == skip or abs_p.startswith(skip + ".") or abs_p.startswith(skip + "-")


def _fingerprint_of(root: Path, rels: list[str], db: str | None = None) -> str:
    h = hashlib.sha1()
    for rel in sorted(rels):
        if _db_artifact(root, rel, db):
            continue
        try:
            st = (root / rel).lstat()
            sig = f"{st.st_mtime_ns} {st.st_size}"
        except OSError:
            sig = "missing"
        h.update(rel.encode("utf-8", "surrogateescape") + b"\0" + sig.encode() + b"\0")
    return h.hexdigest()


def _fingerprint(root: Path, db: str | None = None) -> str | None:
    """Hash of (path, mtime_ns, size) for every file `git ls-files -c -o --exclude-standard` lists, or
    None when git fails. Taken before indexing, so an edit made while the index runs changes it; a
    rename, an add, a delete or an edit changes it too. `db` and its sidecars are left out."""
    if not _git_ok(root):
        return None
    rels = _listed_files(root)
    if rels is None:
        return None
    return _fingerprint_of(root, rels, db)


def _state_matches(db: str, snapshot: str | None) -> bool:
    """True when `<db>.refresh.state` records this snapshot and the database's current mtime."""
    if snapshot is None:
        return False
    state_path = Path(db + ".refresh.state")
    if not state_path.is_file():
        return False
    try:
        saved = json.loads(state_path.read_text(encoding="utf-8"))
        mtime = os.stat(db).st_mtime_ns
    except (OSError, ValueError):
        return False
    return isinstance(saved, dict) and saved.get("files") == snapshot and saved.get("db_mtime_ns") == mtime


def staleness(root: str | Path, db: str) -> bool | None:
    """True when the working tree changed since `db` was built. None when the DB is missing or
    empty, ROOT is not a git repo, or git fails. One `git ls-files` and one lstat per file."""
    db_path = Path(db)
    try:
        if not db_path.is_file() or db_path.stat().st_size == 0:
            return None
        db_mtime = db_path.stat().st_mtime_ns
    except OSError:
        return None
    rootp = Path(root)
    if not _git_ok(rootp):
        return None
    rels = _listed_files(rootp)
    if rels is None:
        return None
    db_abs = str(db_path.resolve())
    state_path = Path(db_abs + ".refresh.state")
    saved = None
    if state_path.is_file():
        try:
            saved = json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            saved = None
    if isinstance(saved, dict) and saved.get("db_mtime_ns") == db_mtime and isinstance(saved.get("files"), str):
        return _fingerprint_of(rootp, rels, db_abs) != saved["files"]
    for rel in rels:
        if _db_artifact(rootp, rel, db_abs):
            continue
        try:
            st = (rootp / rel).lstat()
        except OSError:
            continue
        if st.st_mtime_ns > db_mtime:
            return True
    return False


@contextlib.contextmanager
def _refresh_lock(lock: Path):
    """Yield True when this process holds `<db>.refresh.lock`, else False (caller must not wait).
    `flock` (POSIX) or `msvcrt.locking` (Windows): the OS drops either when the process dies, so a
    killed refresh never leaves a stale lock behind. The lock file itself is left in place."""
    lock.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(lock, os.O_CREAT | os.O_RDWR, 0o644)
    held = False
    try:
        try:
            if fcntl is not None:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            elif msvcrt is not None:
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            else:            # no file locking at all: run unlocked rather than never
                pass
            held = True
        except OSError:
            pass
        if not held:
            yield False
            return
        yield True
    finally:
        if held:
            try:
                if fcntl is not None:
                    fcntl.flock(fd, fcntl.LOCK_UN)
                elif msvcrt is not None:
                    os.lseek(fd, 0, os.SEEK_SET)
                    msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
            except OSError:
                pass
        os.close(fd)


def _log(db: str, lines: list[str]) -> None:
    try:
        Path(db + ".refresh.log").write_text("\n".join(lines) + "\n", encoding="utf-8")
    except OSError:
        pass


def _rm(path: Path) -> None:
    for suffix in ("", "-wal", "-shm", "-journal"):
        candidate = Path(str(path) + suffix)
        if candidate.exists():
            try:
                candidate.unlink()
            except OSError:
                pass


def _read_meta(db: str) -> dict:
    if not os.path.isfile(db) or os.path.getsize(db) == 0:
        return {}
    from .core.store import GraphStore
    store = GraphStore(db)
    try:
        return store.meta()
    except Exception:
        return {}
    finally:
        store.db.close()


def _node_count(result: dict, apps: bool) -> int:
    if apps:
        return sum((a.get("nodes") or 0) for a in result.get("apps") or [])
    return int(result.get("nodes") or 0)


def _cleanup_tmp(tmp: str, apps: list[dict] | None) -> None:
    _rm(Path(tmp))
    if not apps:
        return
    from .apps import app_dbs
    for path in app_dbs(tmp, apps).values():
        _rm(Path(path))


def _checkpoint(path: Path) -> None:
    """Fold the WAL into the main file so a later delete of `<tmp>-wal` cannot drop commits."""
    if not path.is_file():
        return
    import sqlite3
    conn = sqlite3.connect(path)
    try:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        conn.close()


def _publish(tmp: str, db: str, apps: list[dict] | None) -> None:
    if apps:
        from .apps import app_dbs
        for name, src in app_dbs(tmp, apps).items():
            dest = app_dbs(db, apps)[name]
            if Path(src).exists():
                _checkpoint(Path(src))
                dest.parent.mkdir(parents=True, exist_ok=True)
                os.replace(src, dest)
                _rm_sidecars(dest)
    _checkpoint(Path(tmp))
    os.replace(tmp, db)
    _rm_sidecars(Path(db))


def _rm_sidecars(db: Path) -> None:
    """Drop a previous WAL so SQLite does not replay it onto the database just replaced."""
    for suffix in ("-wal", "-shm", "-journal"):
        candidate = Path(str(db) + suffix)
        if candidate.exists():
            try:
                candidate.unlink()
            except OSError:
                pass


def _refresh_once(root: Path, db: str, name: str | None, quiet: bool, out) -> int:
    started = time.time()
    stamp = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime(started))
    state_path = Path(db + ".refresh.state")
    try:
        meta = _read_meta(db)
        meta_root = meta.get("root")
        index_root = Path(meta_root).resolve() if meta_root else root
        index_name = meta.get("project") if meta.get("project") else name
        same_tree = index_root == root
        db_exists = os.path.isfile(db) and os.path.getsize(db) > 0
        from .config import ConfigError, load as load_config
        from .indexer import index_project
        try:
            cfg = load_config(index_root)
        except ConfigError as ex:
            _log(db, [f"start {stamp}", f"error {ex}"])
            if not quiet:
                out(f"refresh failed: {ex}")
            return 1
        apps = cfg.get("apps") or None
        snapshot = _fingerprint(index_root, db)
        if apps and snapshot is not None:      # an app root outside the checkout is not in the fingerprint
            from .apps import app_path
            for a in apps:
                try:
                    app_path(index_root, a.get("root") or "").resolve().relative_to(index_root)
                except ValueError:
                    snapshot = None
                    break
        if db_exists and same_tree and _state_matches(db, snapshot):
            _log(db, [f"start {stamp}", f"end {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}",
                      "status up to date"])
            if not quiet:
                out("up to date")
            return 0
        if meta.get("repos") and not apps:
            msg = ("refresh refused: the database is a combined graph (cg link); re-index it with the "
                   "MCP index tool or cg link. The graph is unchanged")
            _log(db, [f"start {stamp}", "status refused", "combined graph"])
            if not quiet:
                out(msg)
            return 1
        stats = (meta.get("stats") or {}) if (db_exists and same_tree) else {}
        py_roots = stats.get("python_roots_flag")
        include_generated = bool(stats.get("include_generated_flag"))
        tmp = db + ".refresh.tmp"
        _cleanup_tmp(tmp, apps)
        try:
            if apps:
                from .apps import index_apps
                result = index_apps(index_root, tmp, cfg, python_roots=py_roots,
                                    include_generated=include_generated)
            else:
                result = index_project(index_root, tmp, index_name, python_roots=py_roots,
                                       include_generated=include_generated)
            nodes = _node_count(result, bool(apps))
            if not nodes:
                _log(db, [f"start {stamp}", f"end {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}",
                          "status refused", "nodes 0"])
                if not quiet:
                    out("refresh refused: 0 nodes; the graph is unchanged")
                return 1
            _publish(tmp, db, apps)
        finally:
            _cleanup_tmp(tmp, apps)
        elapsed = round(time.time() - started, 2)
        _log(db, [f"start {stamp}", f"end {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}",
                  f"nodes {nodes}", f"seconds {elapsed}", "status ok"])
        state = {"files": snapshot, "db_mtime_ns": os.stat(db).st_mtime_ns}
        state_path.write_text(json.dumps(state), encoding="utf-8")
        if not quiet:
            out(f"refreshed: {nodes} nodes in {elapsed}s")
        return 0
    except Exception as ex:  # noqa: BLE001  (hooks must see a nonzero exit, not a traceback)
        _log(db, [f"start {stamp}", f"end {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}",
                  f"error {type(ex).__name__}: {ex}"])
        if not quiet:
            out(f"refresh failed: {type(ex).__name__}: {ex}")
        return 1


# Set by git for hooks (GIT_DIR in a linked worktree, a relative GIT_INDEX_FILE): they would point the
# refresh's own git calls, and the indexer's, at the repository that ran the hook instead of ROOT.
_GIT_LOCATION_ENV = ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR", "GIT_PREFIX",
                     "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_NAMESPACE")
_MAX_PASSES = 3


def refresh(root: str, db: str, name: str | None = None, quiet: bool = False, out=print) -> int:
    """Re-index `root` into `db` when sources changed. Exit 0 when skipped or another refresh holds the lock.

    The pending flag is touched before the lock is tried, and the holder clears it before each pass and
    checks it again after releasing the lock, so a request that loses the race is never dropped."""
    for key in _GIT_LOCATION_ENV:
        os.environ.pop(key, None)
    rootp = Path(root).resolve()
    db_abs = str(Path(db).resolve())
    pending = Path(db_abs + ".refresh.pending")
    lock = Path(db_abs + ".refresh.lock")
    lock.parent.mkdir(parents=True, exist_ok=True)
    pending.touch()
    rc = 0
    passes = 0
    while passes < _MAX_PASSES:
        with _refresh_lock(lock) as held:
            if not held:
                if passes == 0 and not quiet:
                    out("refresh already running; marked pending")
                return rc
            while pending.exists() and passes < _MAX_PASSES:
                try:
                    pending.unlink()
                except OSError:
                    pass
                rc = _refresh_once(rootp, db_abs, name, quiet, out)
                passes += 1
        if not pending.exists():
            break
    return rc
