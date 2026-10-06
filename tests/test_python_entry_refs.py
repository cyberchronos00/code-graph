"""Python entry points and function references: `__main__` blocks, `pkg/__main__.py`, packaging entry points
(PEP 621, Poetry, setup.cfg, setup.py), dispatch tables and calls through them, callbacks, registering decorators,
framework registrations (MCP tools, click / typer commands), function-local imports, short `Class.method` specs,
and test isolation of references. Every fixture is written from scratch in a temp dir."""
import subprocess
import sys
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from cg_code_graph import query as Q  # noqa: E402
from cg_code_graph.core.plugin import GraphBuilder, Project  # noqa: E402
from cg_code_graph.core.store import GraphStore  # noqa: E402
from cg_code_graph.indexer import index_project  # noqa: E402
from cg_code_graph.plugins.python.plugin import PythonPlugin  # noqa: E402
from cg_code_graph.tests_index import isolate_tests  # noqa: E402


def write(root: Path, files: dict) -> Path:
    for rel, body in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(textwrap.dedent(body).lstrip("\n"))
    return root


def build(tmp: Path, name: str, files: dict) -> tuple[GraphStore, Path]:
    root = write(tmp / name, files)
    db = tmp / f"{name}.db"
    stats = index_project(root, db, name)
    st = GraphStore(db)
    st.stats = stats
    return st, db


def cg(*args):
    return subprocess.run([sys.executable, "-m", "cg_code_graph.cli", *map(str, args)], cwd=ROOT, capture_output=True, text=True)


def edges(st, kind, **attrs):
    import json
    out = set()
    for r in st.q("SELECT src, dst, confidence, attrs FROM edges WHERE kind=?", (kind,)):
        a = json.loads(r["attrs"] or "{}")
        if all(a.get(k) == v for k, v in attrs.items()):
            out.add((r["src"], r["dst"], r["confidence"]))
    return out


def pairs(st, kind, **attrs):
    return {(s, d) for s, d, _ in edges(st, kind, **attrs)}


def entry(st, nid):
    r = st.q("SELECT entry_kind FROM nodes WHERE id=?", (nid,))
    return r[0]["entry_kind"] if r else None


CHECKS = {
    "pyproject.toml": '''
        [project]
        name = "checks"
        version = "0.1"

        [project.scripts]
        checks = "checks:main"
        ''',
    "checks.py": '''
        def check_size(item):
            return item["size"] < 10

        def check_owner(item):
            return item.get("owner") is not None

        CHECKS = (check_size, check_owner)

        def validate(item):
            return all(check(item) for check in CHECKS)

        def main():
            print(validate({"size": 1, "owner": "a"}))

        if __name__ == "__main__":
            main()
        ''',
}


def test_issue_repro_dispatch_table_console_script_and_main_block(tmp_path):
    st, db = build(tmp_path, "checks", CHECKS)
    calls = edges(st, "CALLS", via="collection")
    assert ("function:checks.validate", "function:checks.check_size", "resolved") in calls
    assert ("function:checks.validate", "function:checks.check_owner", "resolved") in calls
    assert pairs(st, "REFERENCES_FN", how="collection") == {("module:checks", "function:checks.check_size"),
                                                            ("module:checks", "function:checks.check_owner")}
    out = cg("impact", "checks.check_size", "--db", db, "--no-paths").stdout
    assert "d=1 [function] checks.validate  (call through a collection)" in out
    assert "d=1 [module] checks  (ref: collection)" in out
    assert "d=2 [function] checks.main" in out
    for spec in ("checks.check_size", "checks.validate"):
        out = cg("impact", spec, "--db", db, "--no-paths").stdout
        assert "entry points: 2" in out, out
        assert "main             console script checks" in out and "main             python checks.py" in out
    n = st.node("script:console_scripts:checks")
    assert n["entry_kind"] == "main" and n["file"] == "pyproject.toml" and n["line"] == 6
    assert ("script:console_scripts:checks", "function:checks.main") in pairs(st, "CALLS", via="entry_point")
    assert ("script:checks", "function:checks.main") in pairs(st, "CALLS")
    assert st.node("script:checks")["line"] == 15


def test_dispatch_tables_lists_dicts_registries_and_plugin_instances(tmp_path):
    st, _ = build(tmp_path, "disp", {
        "app/__init__.py": "",
        "app/handlers.py": '''
            def on_create(e): return e
            def on_delete(e): return e
            def on_move(e): return e
            def on_late(e): return e
            def fallback(e): return e

            HANDLERS = {"create": on_create, "delete": on_delete}
            HANDLERS["move"] = on_move
            STEPS = [on_create]
            STEPS.append(on_late)

            def dispatch(kind, e):
                return HANDLERS[kind](e)

            def dispatch_get(kind, e):
                return HANDLERS.get(kind, fallback)(e)

            def run_steps(e):
                for step in STEPS:
                    step(e)

            def run_named(e):
                for name, fn in HANDLERS.items():
                    fn(e)

            def pick(kind):
                handler = HANDLERS[kind]
                return handler(None)
            ''',
        "app/plugins.py": '''
            class Base:
                def run(self): raise NotImplementedError

            class Alpha(Base):
                def run(self): return 1

            class Beta(Base):
                def run(self): return 2

            class Gamma(Base):
                def run(self): return 3

            EXTRA = [Gamma()]
            PLUGINS = [Alpha(), Beta(), *EXTRA]

            def run_all(enabled):
                active = [p for p in PLUGINS if p.run in enabled]
                for p in active:
                    p.run()
            ''',
        "app/machine.py": '''
            class Machine:
                def start(self): return "s"
                def stop(self): return "t"
                TABLE = {"go": start, "halt": stop}

                def fire(self, ev):
                    return self.TABLE[ev](self)
            ''',
    })
    c = pairs(st, "CALLS", via="collection")
    H = "function:app.handlers."
    assert {(H + "dispatch", H + x) for x in ("on_create", "on_delete", "on_move")} <= c
    assert (H + "dispatch", H + "fallback") not in c
    assert {(H + "dispatch_get", H + x) for x in ("on_create", "on_delete", "on_move", "fallback")} <= c
    assert {(H + "run_steps", H + "on_create"), (H + "run_steps", H + "on_late")} <= c
    assert {(H + "run_named", H + "on_create"), (H + "run_named", H + "on_delete")} <= c
    assert (H + "pick", H + "on_delete") in c
    P = "method:app.plugins."
    assert {("function:app.plugins.run_all", P + f"{k}.run") for k in ("Alpha", "Beta", "Gamma")} <= c
    assert (P + "Base.run") not in {d for _, d in c}
    M = "method:app.machine.Machine."
    assert {(M + "fire", M + "start"), (M + "fire", M + "stop")} <= c
    # every table entry is also a reference from the module (or class body) that holds it
    refs = pairs(st, "REFERENCES_FN", how="collection")
    assert ("module:app.handlers", H + "on_late") in refs and ("module:app.machine", M + "start") in refs
    assert ("module:app.handlers", H + "on_move") in pairs(st, "REFERENCES_FN", how="assignment")


def test_calls_through_copies_and_returned_collections(tmp_path):
    st, _ = build(tmp_path, "copies", {
        "app/__init__.py": "",
        "app/plugins.py": '''
            class Base:
                def run(self):
                    raise NotImplementedError

            class Alpha(Base):
                def run(self):
                    return 1

            class Beta(Base):
                def run(self):
                    return 2

            PLUGINS = [Alpha(), Beta()]
            HOOKS = {"a": Alpha, "b": Beta}
            ''',
        "app/runner.py": '''
            import copy
            from copy import deepcopy
            from app.plugins import PLUGINS, HOOKS

            def fresh():
                return [p for p in copy.deepcopy(PLUGINS) if p.run()]

            def setup():
                active = fresh()
                return {"plugins": active, "names": ["x"]}

            def pair():
                return deepcopy(PLUGINS), list(HOOKS.values())

            def run_plan():
                plan = setup()
                for p in plan["plugins"]:
                    p.run()

            def run_pair():
                plugins = pair()[0]
                for p in plugins:
                    p.run()

            def run_shallow():
                for p in PLUGINS.copy():
                    p.run()
                for h in copy.copy(HOOKS).values():
                    h().run()
            ''',
    })
    c = pairs(st, "CALLS", via="collection")
    R, P = "function:app.runner.", "method:app.plugins."
    for f in ("fresh", "run_plan", "run_pair", "run_shallow"):
        assert {(R + f, P + "Alpha.run"), (R + f, P + "Beta.run")} <= c, f
    assert P + "Base.run" not in {d for _, d in c}
    callers = {x["fqn"] for x in Q.impact(st, "app.plugins.Alpha.run")["callers"]}
    assert {"app.runner.fresh", "app.runner.run_plan", "app.runner.run_pair", "app.runner.run_shallow"} <= callers


def test_callbacks_to_stdlib_and_library_apis(tmp_path):
    st, db = build(tmp_path, "cb", {
        "svc/__init__.py": "",
        "svc/work.py": '''
            import atexit
            import functools
            import signal
            import threading
            from concurrent.futures import ThreadPoolExecutor
            from django.db.models.signals import post_save

            def double(x): return 2 * x
            def by_name(x): return x.name
            def background(): return 1
            def job(): return 2
            def cleanup(): return 3
            def on_term(signum, frame): return 4
            def on_saved(sender, **kw): return 5
            def scaled(f, x): return f * x
            def unused(): return 6

            atexit.register(cleanup)
            signal.signal(signal.SIGTERM, on_term)
            post_save.connect(on_saved)

            def go(items):
                xs = list(map(double, items))
                ys = sorted(items, key=by_name)
                threading.Thread(target=background).start()
                with ThreadPoolExecutor() as ex:
                    ex.submit(job)
                return functools.partial(scaled, 3), xs, ys

            class Button:
                def __init__(self, bus):
                    bus.subscribe(self.on_click)
                    print(self.label, self.width)

                def on_click(self, ev):
                    return ev

                @property
                def label(self):
                    return "ok"

                @functools.cached_property
                def width(self):
                    return 3
            ''',
    })
    W = "function:svc.work."
    cb = pairs(st, "REFERENCES_FN", how="callback")
    assert {(W + "go", W + x) for x in ("double", "by_name", "background", "job", "scaled")} <= cb
    assert {("module:svc.work", W + x) for x in ("cleanup", "on_term", "on_saved")} <= cb
    assert ("method:svc.work.Button.__init__", "method:svc.work.Button.on_click") in cb
    assert not [d for _, d in cb if d == W + "unused"]
    assert not [d for _, d in cb if d.endswith(("Button.label", "Button.width"))]   # reading a property is no reference
    assert all(conf == "resolved" for *_, conf in edges(st, "REFERENCES_FN", how="callback"))
    out = cg("impact", "svc.work.double", "--db", db, "--no-paths").stdout
    assert "d=1 [svc] svc.work.go  (ref: callback)" in out
    # MCP callers names the reference as such
    from cg_code_graph import mcp_server as M
    old = dict(M.STATE)
    try:
        M.STATE["db"] = str(db)
        txt = M.callers("svc.work.double")
        assert "svc.work.go  ref@" in txt
        assert "by reference (1): svc.work.go (callback)" in M.impact("svc.work.double")
    finally:
        M.STATE.clear(); M.STATE.update(old)


def test_registering_decorators(tmp_path):
    st, db = build(tmp_path, "deco", {
        "svc/__init__.py": "",
        "svc/registry.py": '''
            import functools

            REGISTRY = {}

            def register(fn):
                REGISTRY[fn.__name__] = fn
                return fn

            def hook(name):
                def wrap(fn):
                    REGISTRY[name] = fn
                    return fn
                return wrap

            class Registry:
                def __init__(self):
                    self.items = []
                def add(self, fn):
                    self.items.append(fn)
                    return fn

            registry = Registry()

            @functools.singledispatch
            def render(x): return str(x)
            ''',
        "svc/jobs.py": '''
            import functools
            from sanic import Sanic
            from .registry import register, hook, registry, render

            app = Sanic(__name__)

            @register
            def nightly(): return 1

            @hook("startup")
            def warm(): return 2

            @registry.add
            def export(): return 3

            @render.register
            def _(x: int): return f"int {x}"

            @app.route("/ping")
            def ping(): return "pong"

            @functools.lru_cache
            def cached(): return 4

            @property
            def nope(self): return 5
            ''',
    })
    R, J = "svc.registry.", "function:svc.jobs."
    dec = edges(st, "REFERENCES_FN", how="decorator")
    assert ("function:" + R + "register", J + "nightly", "resolved") in dec
    assert ("function:" + R + "hook", J + "warm", "resolved") in dec
    assert ("method:" + R + "Registry.add", J + "export", "resolved") in dec
    assert ("function:" + R + "render", J + "_", "resolved") in dec          # singledispatch: render dispatches to _
    assert ("module:svc.jobs", J + "ping", "heuristic") in dec              # external registering decorator
    assert not [d for _, d, _ in dec if d in (J + "cached", J + "nope")]
    # decorator applications are calls (the decorator runs at import time)
    deco_calls = pairs(st, "CALLS", via="decorator")
    assert {("module:svc.jobs", "function:" + R + "register"), ("module:svc.jobs", "function:" + R + "hook"),
            ("module:svc.jobs", "method:" + R + "Registry.add")} <= deco_calls
    out = cg("impact", "svc.jobs.nightly", "--db", db, "--no-paths").stdout
    assert "d=1 [svc] svc.registry.register  (ref: decorator)" in out


def test_framework_registrations_mcp_click_typer(tmp_path):
    st, db = build(tmp_path, "fw", {
        "pyproject.toml": '''
            [project]
            name = "fw"
            version = "1"
            [project.scripts]
            fw = "tool.cli:cli"
            fw-typer = "tool.tcli:app"
            ''',
        "tool/__init__.py": "",
        "tool/server.py": '''
            import functools
            from mcp.server.fastmcp import FastMCP

            mcp = FastMCP("demo")

            def lookup(q): return q

            @mcp.tool()
            def search(q: str) -> str:
                return lookup(q)

            def status() -> str:
                return "ok"

            mcp.add_tool(status)

            def tool(fn):
                """Wraps a tool and registers the wrapper."""
                @functools.wraps(fn)
                def served(*a, **k):
                    return fn(*a, **k)
                mcp.tool()(served)
                return fn

            @tool
            def stats() -> str:
                return lookup("stats")

            if __name__ == "__main__":
                mcp.run()
            ''',
        "tool/cli.py": '''
            import click

            @click.group()
            def cli():
                pass

            @cli.command()
            def build():
                return 1

            @click.command()
            def standalone():
                return 2

            cli.add_command(standalone)
            ''',
        "tool/tcli.py": '''
            import typer

            app = typer.Typer()

            @app.command()
            def hello(name: str):
                return name

            @app.callback()
            def root():
                pass

            if __name__ == "__main__":
                app()
            ''',
    })
    S = "function:tool.server."
    assert entry(st, S + "search") == "message_handler" and entry(st, S + "status") == "message_handler"
    assert entry(st, S + "stats") == "message_handler"          # registered through the local wrapper `tool`
    assert entry(st, S + "tool") is None and entry(st, S + "lookup") is None
    C = "function:tool.cli."
    assert entry(st, C + "cli") == "cli_command" and entry(st, C + "build") == "cli_command"
    assert entry(st, C + "standalone") == "cli_command"
    assert (C + "cli", C + "build") in pairs(st, "REFERENCES_FN", how="decorator")   # the group holds its commands
    T = "function:tool.tcli."
    assert entry(st, T + "hello") == "cli_command" and entry(st, T + "root") == "cli_command"
    # console scripts: the click group function, and a typer app object (its registered commands)
    assert ("script:console_scripts:fw", C + "cli") in pairs(st, "CALLS", via="entry_point")
    assert {("script:console_scripts:fw-typer", T + "hello"), ("script:console_scripts:fw-typer", T + "root")} <= \
        pairs(st, "REFERENCES_FN", how="entry point")
    assert ("script:tool.tcli", T + "hello") in pairs(st, "REFERENCES_FN", via="registered command")
    out = cg("impact", "tool.server.lookup", "--db", db, "--no-paths").stdout
    assert "message_handler  search" in out and "message_handler  stats" in out
    out = cg("impact", "tool.cli.build", "--db", db, "--no-paths").stdout
    assert "main             console script fw" in out and "cli_command      build" in out
    regs = st.stats["plugins"]["python"]["registrations"]
    assert regs["mcp:message_handler"] == 3 and regs["click:cli_command"] == 3 and regs["typer:cli_command"] == 2


def test_dunder_main_module_and_packaging_entry_points(tmp_path):
    st, db = build(tmp_path, "pkgs", {
        # PEP 621 + plugin group, nested project with a src/ layout (resolved through the detected roots)
        "svc/pyproject.toml": '''
            [project]
            name = "svc"
            version = "1"
            [project.gui-scripts]
            svc-gui = "svc.gui:launch"
            [project.entry-points."pytest11"]
            svc = "svc.plugin"
            [project.entry-points."svc.exporters"]
            csv = "svc.export:CsvExporter"
            ''',
        "svc/src/svc/__init__.py": "",
        "svc/src/svc/__main__.py": '''
            from .app import run
            run()
            ''',
        "svc/src/svc/app.py": "def run():\n    return 1\n",
        "svc/src/svc/gui.py": "def launch():\n    return 2\n",
        "svc/src/svc/plugin.py": "def pytest_addoption(parser):\n    return parser\n\ndef _private():\n    return 0\n",
        "svc/src/svc/export.py": '''
            class CsvExporter:
                def __init__(self):
                    self.rows = []
                def write(self, row):
                    self.rows.append(row)
                def _flush(self):
                    pass
            ''',
        # Poetry
        "poet/pyproject.toml": '''
            [tool.poetry]
            name = "poet"
            version = "1"
            [tool.poetry.scripts]
            poet = "poet.main:run"
            poet-obj = { reference = "poet.main:other", type = "console" }
            [tool.poetry.plugins."poet.hooks"]
            hook = "poet.main:hook"
            ''',
        "poet/poet/__init__.py": "",
        "poet/poet/main.py": "def run(): return 1\ndef other(): return 2\ndef hook(): return 3\n",
        # setup.cfg
        "cfgp/setup.cfg": '''
            [metadata]
            name = cfgp
            [options.entry_points]
            console_scripts =
                cfgp = cfgp.cli:main
                cfgp-broken = cfgp.missing:main
            ''',
        "cfgp/cfgp/__init__.py": "",
        "cfgp/cfgp/cli.py": "def main():\n    return 1\n",
        # setup.py literal
        "legacy/setup.py": '''
            from setuptools import setup
            ENTRY = {"console_scripts": ["legacy = legacy.tool:main [extra]"]}
            setup(name="legacy", entry_points=ENTRY)
            ''',
        "legacy/legacy/__init__.py": "",
        "legacy/legacy/tool.py": "def main():\n    return 1\n",
    })
    sc = {r["id"]: r for r in st.q("SELECT id, name, entry_kind, file, attrs FROM nodes WHERE kind='script'")}
    assert sc["script:svc.__main__"]["name"] == "python -m svc" and sc["script:svc.__main__"]["entry_kind"] == "main"
    assert ("script:svc.__main__", "function:svc.app.run") in pairs(st, "CALLS")
    assert sc["script:gui_scripts:svc-gui"]["name"] == "GUI script svc-gui"
    assert ("script:gui_scripts:svc-gui", "function:svc.gui.launch") in pairs(st, "CALLS", via="entry_point")
    assert sc["script:pytest11:svc"]["entry_kind"] == "public_api"
    ep = pairs(st, "REFERENCES_FN", how="entry point")
    assert ("script:pytest11:svc", "function:svc.plugin.pytest_addoption") in ep
    assert ("script:pytest11:svc", "function:svc.plugin._private") not in ep
    assert ("script:svc.exporters:csv", "method:svc.export.CsvExporter.write") in ep
    assert ("script:svc.exporters:csv", "method:svc.export.CsvExporter.__init__") in pairs(st, "CALLS", via="entry_point")
    assert ("script:console_scripts:poet", "function:poet.main.run") in pairs(st, "CALLS", via="entry_point")
    assert ("script:console_scripts:poet-obj", "function:poet.main.other") in pairs(st, "CALLS", via="entry_point")
    assert sc["script:poet.hooks:hook"]["entry_kind"] == "public_api"
    assert ("script:console_scripts:cfgp", "function:cfgp.cli.main") in pairs(st, "CALLS", via="entry_point")
    assert sc["script:console_scripts:cfgp"]["file"] == "cfgp/setup.cfg"
    assert ("script:console_scripts:legacy", "function:legacy.tool.main") in pairs(st, "CALLS", via="entry_point")
    py = st.stats["plugins"]["python"]
    assert py["script_entries"] == {"main_blocks": 1, "packaging": 8}
    assert py["entry_points_unresolved"]["samples"] == ["cfgp/setup.cfg [options.entry_points]: cfgp-broken = cfgp.missing:main"]
    out = cg("impact", "svc.app.run", "--db", db, "--no-paths").stdout
    assert "main             python -m svc" in out


def test_function_local_imports_resolve_per_def(tmp_path):
    st, _ = build(tmp_path, "loc", {
        "pkg/__init__.py": "",
        "pkg/routes.py": "def render_routes():\n    return 1\n",
        "pkg/concepts.py": "def resolutions():\n    return 2\n",
        "pkg/server.py": '''
            def concepts():
                from .concepts import resolutions as R
                return R()

            def routes():
                from . import routes as R
                return R.render_routes()
            ''',
    })
    c = pairs(st, "CALLS")
    assert ("function:pkg.server.routes", "function:pkg.routes.render_routes") in c
    assert ("function:pkg.server.concepts", "function:pkg.concepts.resolutions") in c


def test_short_class_method_spec(tmp_path):
    st, db = build(tmp_path, "spec", {
        "lib/__init__.py": "",
        "lib/deep/__init__.py": "",
        "lib/deep/prog.py": "class Prog:\n    def load(self):\n        return 1\n\ndef use():\n    return Prog().load()\n",
    })
    assert Q.resolve_targets(st, "Prog.load") == ["method:lib.deep.prog.Prog.load"]
    assert Q.resolve_targets(st, "prog.Prog.load") == ["method:lib.deep.prog.Prog.load"]
    out = cg("impact", "Prog.load", "--db", db, "--no-paths").stdout
    assert "d=1 [lib/deep] lib.deep.prog.use" in out


def test_functions_referenced_only_from_tests_stay_uncalled(tmp_path):
    """Test code (attrs.test, set by the test indexing pass) holding a function in a table or passing it as a callback
    does not make it a caller: its REFERENCES_FN edges become TEST_CALLS like its calls."""
    root = write(tmp_path / "iso", {
        "app/__init__.py": "",
        "app/core.py": "def helper(x):\n    return x\n\ndef used(x):\n    return x\n\ndef api(x):\n    return used(x)\n",
        "tests/__init__.py": "",
        "tests/test_core.py": '''
            from app.core import helper, api

            CASES = [helper]

            def test_helper():
                assert list(map(helper, [1])) == [1]
                for case in CASES:
                    case(1)

            def test_api():
                assert api(1) == 1
            ''',
    })
    b = GraphBuilder()
    PythonPlugin().index(Project(root=root, name="iso"), b, [])
    for n in b.nodes.values():
        if (n.file or "").startswith("tests/"):
            n.attrs = {**(n.attrs or {}), "test": True}
    before = {(e.src, e.kind) for e in b.edges.values() if e.dst == "function:app.core.helper"}
    assert ("module:tests.test_core", "REFERENCES_FN") in before and ("function:tests.test_core.test_helper", "CALLS") in before
    isolate_tests(b)
    from cg_code_graph.core.model import PROPAGATING
    into = [e for e in b.edges.values() if e.dst == "function:app.core.helper" and e.kind in PROPAGATING]
    assert into == []
    assert {e.attrs.get("orig") for e in b.edges.values() if e.dst == "function:app.core.helper" and e.kind == "TEST_CALLS"} \
        >= {"REFERENCES_FN", "CALLS"}
    assert [e for e in b.edges.values() if e.dst == "function:app.core.used" and e.kind == "CALLS"]


def test_django_wired_views_keep_their_framework_edges_only(tmp_path):
    """A view a Django route points at (path("x/", views.x), @router.get) is not also listed as referenced by its urls
    module: the framework edge supersedes the syntactic reference."""
    db = tmp_path / "bookstore.db"
    stats = index_project(ROOT / "examples" / "bookstore-django", db, "bookstore")
    st = GraphStore(db)
    wired = {r["dst"] for r in st.q("SELECT e.dst FROM edges e JOIN nodes n ON n.id = e.src WHERE n.kind = 'route'")}
    assert wired
    mod_refs = {r["dst"] for r in st.q("SELECT dst FROM edges WHERE kind = 'REFERENCES_FN' AND src LIKE 'module:%'")}
    assert not wired & mod_refs
    assert stats["plugins"]["python"]["references_superseded"] > 0
