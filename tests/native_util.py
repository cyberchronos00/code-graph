"""Shared helpers for the Rust / C / C++ plugin tests."""
import contextlib
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from cg_code_graph.indexer import index_project  # noqa: E402
from cg_code_graph.plugins.native import runner  # noqa: E402

GATES = ROOT / "examples" / "native.gates.json"


def have_tree_sitter() -> bool:
    try:
        import tree_sitter  # noqa: F401
        import tree_sitter_c  # noqa: F401
        import tree_sitter_cpp  # noqa: F401
        import tree_sitter_rust  # noqa: F401
        return True
    except ImportError:
        return False


TS_SKIP = "tree-sitter grammars not installed: pip install -r requirements.txt (tree-sitter, tree-sitter-rust/-c/-cpp)"


def _exits_ok(tool: str, args: tuple[str, ...]) -> tuple[bool, str]:
    """(exited 0, combined output). A rustup proxy with no component or no default toolchain exits non-zero."""
    try:
        r = subprocess.run([tool, *args], capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired):
        return False, ""
    return r.returncode == 0, ((r.stdout or "") + "\n" + (r.stderr or "")).strip()


def rust_analyzer():
    """Path to a rust-analyzer that can run, or None.

    A binary on PATH is not enough: `rust-analyzer --version` must exit 0, and when rustup is installed it must
    report a default toolchain. An empty rustup proxy exits 1 and the Rust plugin stays on the heuristic layer.
    """
    tool = runner.find_tool("CG_RUST_ANALYZER", ["rust-analyzer"], [Path.home() / ".cargo" / "bin"])
    if not tool or not _exits_ok(tool, ("--version",))[0]:
        return None
    rustup = shutil.which("rustup")
    if rustup:
        ok, text = _exits_ok(rustup, ("show", "active-toolchain"))
        low = text.lower()
        if not ok or "no default toolchain" in low or "no active toolchain" in low:
            return None
    return tool


def scip_clang():
    return runner.find_tool("CG_SCIP_CLANG", ["scip-clang"], [Path.home() / ".local" / "bin"])


@contextlib.contextmanager
def env(**kv):
    old = {k: os.environ.get(k) for k in kv}
    try:
        for k, v in kv.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = str(v)
        yield
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def index(root: Path, gates: bool = False, **envs) -> tuple[Path, dict]:
    db = Path(tempfile.mkdtemp()) / "g.db"
    with env(**envs):
        res = index_project(root, db, root.name, gates=str(GATES) if gates else None)
    return db, res


def stats_of(res: dict, plugin: str) -> dict:
    st = res.get("plugins", res.get("stats", {}).get("plugins", {})) if isinstance(res, dict) else {}
    return st.get(plugin, {})


def cmake_compdb(src: Path) -> Path | None:
    """Configure (not build) a CMake project into a temp dir with CMAKE_EXPORT_COMPILE_COMMANDS=ON."""
    cmake = shutil.which("cmake")
    if not cmake:
        return None
    import subprocess
    out = Path(tempfile.mkdtemp()) / "build"
    r = subprocess.run([cmake, "-S", str(src), "-B", str(out), "-DCMAKE_EXPORT_COMPILE_COMMANDS=ON"], capture_output=True, text=True)
    p = out / "compile_commands.json"
    return p if r.returncode == 0 and p.exists() else None


class DB:
    def __init__(self, path: Path):
        self.c = sqlite3.connect(path)
        self.c.row_factory = sqlite3.Row
        self.path = path

    def q(self, sql, *a):
        return self.c.execute(sql, a).fetchall()

    def node(self, nid):
        r = self.q("SELECT * FROM nodes WHERE id=?", nid)
        return dict(r[0]) if r else None

    def edges(self, kind, src=None, dst=None):
        sql, a = "SELECT * FROM edges WHERE kind=?", [kind]
        if src:
            sql += " AND src=?"; a.append(src)
        if dst:
            sql += " AND dst=?"; a.append(dst)
        return [dict(r) for r in self.c.execute(sql, a).fetchall()]

    def has(self, kind, src, dst):
        return bool(self.edges(kind, src, dst))

    def entry(self, nid):
        n = self.node(nid)
        return n and n["entry_kind"]
