"""pytest and unittest test code as graph nodes (stdlib `ast`, `configparser`, `tomllib`; no code execution).

Discovery (defaults follow pytest; a pytest config at the indexed root or a nested project root overrides them):
  * test files: `python_files` (default `test_*.py`, `*_test.py`), Django / unittest `tests.py`, `conftest.py`,
    every file under a `tests/` or `test/` directory or a configured `testpaths` directory (an application package
    named in `testpaths` contributes only its matching files), and modules named in `pytest_plugins`. A module under `tests/` that application code imports (a library's own test utilities) stays
    application code, and so does an imported `tests.py` that defines no test case;
  * every node the Python plugin declared in test code gets `attrs.test = True`, so `isolate_tests()`
    (cg_code_graph/tests_index.py) turns its calls, references and collection calls into TEST_* edges.

Test cases (`test:<qual>` nodes, entry kind `test`, `attrs.framework` = pytest | unittest), each with TEST_CALLS to
the function or method that holds its code:
  * pytest: functions matching `python_functions` (default `test*`) in collected files, and the matching methods of
    classes matching `python_classes` (default `Test*`, no `__init__`), inherited methods included;
    `@pytest.mark.parametrize` -> `attrs.params` (argument names, literal ids or values, case count), other marks ->
    `attrs.marks`;
  * unittest: `test*` methods of `unittest.TestCase` subclasses (Django `TestCase` / `TransactionTestCase` /
    `SimpleTestCase`, DRF `APITestCase`, ...), with TEST_CALLS to `setUp` / `setUpClass` / `setUpTestData` /
    `tearDown` (and pytest's xunit `setup_method` / `setup_module` ...) of the class and module.

Fixtures: `@pytest.fixture` functions in the test module, its classes, every `conftest.py` up the directory tree and
`pytest_plugins` modules. A test gets TEST_USES to every fixture it requests (parameters, `usefixtures`,
`request.getfixturevalue('x')`) and to the `autouse=True` fixtures in its scope; a fixture gets TEST_USES to the
fixtures it requests, resolved in its own scope (a fixture overriding a fixture of the same name reaches the outer one).

HTTP tests (TEST_HTTP -> the matching route, matched like client calls in `cg link`): requests on a test client
(`self.client.get(...)`, a `client` / `admin_client` / `api_client` fixture, `APIClient()`, `TestClient(app)`,
`app.test_client()`, `httpx.AsyncClient`), with the URL evaluated from literals, f-strings, `%` / `+` / `.format`,
local variables, class attributes and `self.x = ...` in setUp, or `reverse('ns:name')` / `reverse_lazy` resolved
through the route names the Django plugin indexed. Requests are collected during indexing and matched after the
framework plugins built their routes (`match_http`).
"""
from __future__ import annotations

import ast
import configparser
import fnmatch
import re
from collections import defaultdict
from pathlib import Path

from ...core.model import EXACT, HEURISTIC, RESOLVED

DEFAULT_FILES = ("test_*.py", "*_test.py")
DEFAULT_CLASSES = ("Test",)
DEFAULT_FUNCTIONS = ("test",)
EXTRA_FILES = ("tests.py",)                      # Django / unittest discovery (`test*.py`) beyond pytest's defaults
TEST_DIRS = {"tests", "test"}
CONFIG_FILES = ("pytest.ini", ".pytest.ini", "pyproject.toml", "tox.ini", "setup.cfg")
SETUP_METHODS = ("setUp", "asyncSetUp", "setUpClass", "setUpTestData", "tearDown", "asyncTearDown", "tearDownClass",
                 "setup_method", "teardown_method", "setup_class", "teardown_class", "setup", "teardown")
SETUP_MODULE = ("setUpModule", "tearDownModule", "setup_module", "teardown_module", "setup_function", "teardown_function")
HTTP_VERBS = {"get": "GET", "post": "POST", "put": "PUT", "patch": "PATCH", "delete": "DELETE", "head": "HEAD",
              "options": "OPTIONS", "trace": "TRACE"}
GENERIC_VERBS = {"generic", "request"}          # client.generic("POST", url) (Django), client.request("POST", url)
from ..pyweb.values import str_value
URL_NAME_FUNCS = {"reverse", "reverse_lazy", "resolve_url", "url_for", "url_path_for"}   # Django, Flask, Starlette
LOCAL_HOSTS = re.compile(r"^[a-z]+://(testserver|localhost|127\.0\.0\.1|0\.0\.0\.0|test|example\.com|\[::1\])(:\d+)?(?=/|$)",
                         re.I)


def _split(v) -> list[str]:
    if isinstance(v, str):
        return [x for x in re.split(r"[\s,]+", v) if x]
    if isinstance(v, (list, tuple)):
        return [str(x) for x in v if str(x).strip()]
    return []


def _ini_section(path: Path, section: str) -> dict | None:
    cp = configparser.ConfigParser(interpolation=None, strict=False)
    try:
        cp.read_string(path.read_text(encoding="utf-8", errors="replace"))
    except (configparser.Error, OSError, UnicodeError):
        return None
    if not cp.has_section(section):
        return None
    return dict(cp.items(section))


def read_pytest_config(d: Path) -> tuple[str, dict] | None:
    """(file, options) of the pytest config in directory d, in pytest's own precedence order."""
    for fn in CONFIG_FILES:
        p = d / fn
        if not p.is_file():
            continue
        if fn in ("pytest.ini", ".pytest.ini"):
            return fn, _ini_section(p, "pytest") or {}
        if fn == "pyproject.toml":
            try:
                import tomllib
                data = tomllib.loads(p.read_text(encoding="utf-8", errors="replace"))
            except Exception:  # noqa: BLE001  (invalid TOML: no pytest settings from it)
                continue
            tool = data.get("tool") if isinstance(data.get("tool"), dict) else {}
            pt = tool.get("pytest") if isinstance(tool.get("pytest"), dict) else None
            if pt is not None:
                opts = pt.get("ini_options") if isinstance(pt.get("ini_options"), dict) else pt
                return f"{fn} [tool.pytest]", opts
            continue
        sect = "pytest" if fn == "tox.ini" else "tool:pytest"
        opts = _ini_section(p, sect)
        if opts is not None:
            return f"{fn} [{sect}]", opts
    return None


class Discovery:
    """pytest naming rules for one project directory."""

    def __init__(self, base: str, source: str | None = None, opts: dict | None = None):
        opts = opts or {}
        self.base = base
        self.source = source
        self.files = tuple(_split(opts.get("python_files"))) or DEFAULT_FILES
        self.classes = tuple(_split(opts.get("python_classes"))) or DEFAULT_CLASSES
        self.functions = tuple(_split(opts.get("python_functions"))) or DEFAULT_FUNCTIONS
        self.testpaths = tuple(p.strip("/") for p in _split(opts.get("testpaths")) if p.strip("/") not in ("", "."))

    @staticmethod
    def _name_match(name: str, pats) -> bool:
        """pytest: a pattern with glob characters is a glob, otherwise a prefix."""
        for p in pats:
            if any(ch in p for ch in "*?["):
                if fnmatch.fnmatchcase(name, p):
                    return True
            elif name.startswith(p):
                return True
        return False

    def collects(self, rel: str) -> bool:
        return self.matches_files(rel) or rel.rsplit("/", 1)[-1] in EXTRA_FILES

    def matches_files(self, rel: str) -> bool:
        """`python_files` match (not counting the Django / unittest `tests.py` name)."""
        base = rel.rsplit("/", 1)[-1]
        return any(fnmatch.fnmatchcase(base, p) or ("/" in p and fnmatch.fnmatchcase(rel, p)) for p in self.files)

    def is_test_function(self, name: str) -> bool:
        return self._name_match(name, self.functions)

    def is_test_class(self, name: str) -> bool:
        return self._name_match(name, self.classes)

    def as_dict(self) -> dict:
        out = {"dir": self.base or ".", "source": self.source}
        for k, dflt in (("files", DEFAULT_FILES), ("classes", DEFAULT_CLASSES), ("functions", DEFAULT_FUNCTIONS)):
            if getattr(self, k) != dflt:
                out[f"python_{k}"] = list(getattr(self, k))
        if self.testpaths:
            out["testpaths"] = list(self.testpaths)
        return out


def _deco_name(d) -> str:
    from .plugin import dotted
    e = d.func if isinstance(d, ast.Call) else d
    return dotted(e) or ""


def _kw(call, name):
    if isinstance(call, ast.Call):
        for k in call.keywords:
            if k.arg == name:
                return k.value
    return None


def _text(e, limit=60) -> str:
    try:
        s = ast.unparse(e)
    except Exception:  # noqa: BLE001
        s = "?"
    return s if len(s) <= limit else s[:limit - 1] + "…"


def _strs(e) -> list[str] | None:
    """'a, b' / ['a', 'b'] / ('a',) -> names; None when not literal."""
    if isinstance(e, ast.Constant) and isinstance(e.value, str):
        return [x.strip() for x in e.value.split(",") if x.strip()]
    if isinstance(e, (ast.List, ast.Tuple)) and all(isinstance(x, ast.Constant) and isinstance(x.value, str) for x in e.elts):
        return [x.value for x in e.elts]
    return None


def _ph(e) -> str:
    """Placeholder name for a value interpolated into a URL: `{book.pk}` -> {pk}, `{slug}` -> {slug}."""
    if isinstance(e, ast.FormattedValue):
        e = e.value
    if isinstance(e, ast.Call) and isinstance(e.func, ast.Name) and e.func.id == "str" and e.args:
        e = e.args[0]
    if isinstance(e, ast.Name):
        return e.id
    if isinstance(e, ast.Attribute):
        return e.attr
    if isinstance(e, ast.Subscript) and isinstance(e.slice, ast.Constant) and isinstance(e.slice.value, str):
        return re.sub(r"\W", "_", e.slice.value) or "p"
    return "p"


class PyTests:
    def __init__(self, prog, builder):
        from .plugin import Ctx
        self.prog, self.b, self.Ctx = prog, builder, Ctx
        self.st: dict = {}
        self.test_files: dict[str, str] = {}     # rel -> why (pattern | conftest | tests dir | testpaths | pytest_plugins)
        self.pending: list[tuple] = []          # HTTP requests found in test code, matched in match_http()
        self.param_values: dict = {}            # parametrize literals of the function collect_http() is reading
        self.fixtures_in: dict = defaultdict(dict)   # scope key -> {name: FuncInfo}
        self.fixture_info: dict = {}            # FuncInfo.id -> {name, autouse, scope, params}
        self._chains: dict = {}                 # (class qual, module) -> (fixture lookup chain, autouse fixtures)
        self._own: dict = {}                    # FuncInfo.id -> (parameters, usefixtures, getfixturevalue names)

    # ------------------------------------------------------------------ discovery
    def discoveries(self) -> list[Discovery]:
        root = self.prog.root
        try:
            projects = [""] + self.prog.root_plan._nested_projects()
        except Exception:  # noqa: BLE001
            projects = [""]
        out = []
        for pd in projects:
            got = read_pytest_config(root / pd if pd else root)
            if got or not pd:
                out.append(Discovery(pd, *(got or (None, None))))
        return sorted(out, key=lambda d: -len(d.base))

    def rootdir_aliases(self) -> int:
        """pytest's default import mode puts the directory of a test file that is not in a package on sys.path, so
        its neighbours import each other by bare name (`from helpers import build`, `from conftest import X`). Those
        names become aliases of the modules below such a directory (never replacing an existing name)."""
        prog = self.prog
        self.discs = self.discoveries()
        files = set(prog.by_file)
        dirs = set()
        for rel in files:
            d, _, base = rel.rpartition("/")
            if (base == "conftest.py" or self.discovery_for(rel).collects(rel)) and \
                    (f"{d}/__init__.py" if d else "__init__.py") not in files:
                dirs.add(d)
        n = 0
        for d in dirs:
            pre = f"{d}/" if d else ""
            for rel, m in prog.by_file.items():
                if not rel.startswith(pre) or (not d and "/" in rel):
                    continue
                name = rel[len(pre):-3].replace("/", ".")
                if name.endswith(".__init__"):
                    name = name[:-len(".__init__")]
                if not name.isidentifier() and not all(x.isidentifier() for x in name.split(".")):
                    continue
                if name not in prog.alias:
                    prog.alias[name] = m
                    n += 1
        return n

    def discovery_for(self, rel: str) -> Discovery:
        for d in self.discs:
            if not d.base or rel.startswith(d.base + "/"):
                return d
        return self.discs[-1]

    def whole_test_dir(self, d: str) -> bool:
        """A `testpaths` directory is test code as a whole when it is a test directory (`tests/`, `testing/`, ...) or
        not an importable package. An application package named in `testpaths` (`testpaths = ["app"]`) is only
        searched: pytest collects the files in it that match `python_files`, so its other modules stay application
        code."""
        name = d.rsplit("/", 1)[-1].lower()
        return name in TEST_DIRS or name.startswith("test") or f"{d}/__init__.py" not in self.prog.by_file

    def classify(self) -> None:
        prog = self.prog
        self.discs = self.discoveries()
        roots = {r.path for r in prog.root_plan.roots.values() if r.path}
        tp_dirs = []
        for d in self.discs:
            for tp in d.testpaths:
                full = f"{d.base}/{tp}" if d.base else tp
                for x in sorted(prog.root_plan.dirs | {full}) if any(ch in tp for ch in "*?[") else [full]:
                    if fnmatch.fnmatchcase(x, full) and x not in roots and not any(r.startswith(x + "/") for r in roots) \
                            and self.whole_test_dir(x):
                        tp_dirs.append(x)
        # a tests/ or test/ directory counts when some test file or conftest.py lives below it (a `test/` package of
        # Jinja test plugins inside an application package does not)
        holding = set()
        for rel in prog.by_file:
            d = self.discovery_for(rel)
            if rel.rsplit("/", 1)[-1] == "conftest.py" or d.collects(rel):
                parts = rel.split("/")[:-1]
                holding.update("/".join(parts[:i + 1]) for i, p in enumerate(parts) if p in TEST_DIRS)
        weak = set()
        for rel, m in prog.by_file.items():
            d = self.discovery_for(rel)
            parts = rel.split("/")
            if rel.rsplit("/", 1)[-1] == "conftest.py":
                self.test_files[rel] = "conftest"
            elif d.collects(rel):
                self.test_files[rel] = "pattern"
                if not d.matches_files(rel) and not self.defines_tests(m, d):
                    weak.add(rel)       # a module named tests.py without test cases (application code if imported)
            elif any(rel.startswith(t + "/") for t in tp_dirs):
                self.test_files[rel] = "testpaths"
            elif any(p in TEST_DIRS and "/".join(parts[:i + 1]) in holding for i, p in enumerate(parts[:-1])):
                self.test_files[rel] = "tests dir"
                weak.add(rel)
        # modules named in pytest_plugins are fixture providers
        for rel, why in list(self.test_files.items()):
            m = prog.by_file[rel]
            for val, _ln, _ann in m.vars.get("pytest_plugins", ()):
                for name in _strs(val) or []:
                    pm = prog.module(name)
                    if pm is not None and pm.file not in self.test_files:
                        self.test_files[pm.file] = "pytest_plugins"
        # a library's own test utilities (django/test/, a package's testing helpers under test/), and a `tests.py`
        # module without test cases, imported by application code stay application code
        if weak:
            imps = defaultdict(set)
            for e in self.b.edges.values():
                if e.kind == "IMPORTS":
                    imps[e.dst].add(e.src)
            app = {f"module:{m.name}" for rel, m in prog.by_file.items() if rel not in self.test_files}
            changed = True
            while changed:
                changed = False
                for rel in list(weak):
                    mid = f"module:{prog.by_file[rel].name}"
                    pkg_dir = rel[:-len("__init__.py")] if rel.endswith("/__init__.py") else None
                    if imps.get(mid, set()) & app or (pkg_dir and any(
                            r.startswith(pkg_dir) and r not in self.test_files for r in prog.by_file)):
                        weak.discard(rel)
                        del self.test_files[rel]
                        app.add(mid)
                        changed = True

    def defines_tests(self, m, disc: Discovery) -> bool:
        """The module holds a test case: a top-level test function, a `Test*` class or a TestCase subclass."""
        if any(disc.is_test_function(n) for n in m.funcs):
            return True
        return any(disc.is_test_class(c.name) or self.is_unittest_class(c) for c in m.classes.values())

    # ------------------------------------------------------------------ marking
    def mark(self) -> int:
        n = 0
        tf = self.test_files
        for node in self.b.nodes.values():
            if node.lang == "python" and node.file in tf and node.kind in ("module", "class", "function", "method", "script"):
                node.attrs = {**(node.attrs or {}), "test": True}
                if node.kind == "script" and node.entry_kind:
                    node.entry_kind = "test"     # `python tests/test_x.py` runs the tests
                n += 1
        return n

    # ------------------------------------------------------------------ fixtures
    @staticmethod
    def fixture_deco(f):
        for d in f.decorators:
            nm = _deco_name(d).split(".")[-1]
            if nm in ("fixture", "yield_fixture"):
                return d
        return None

    def collect_fixtures(self) -> None:
        prog = self.prog
        for f in prog.funcs.values():
            d = self.fixture_deco(f)
            if d is None:
                continue
            name = None
            nv = _kw(d, "name")
            if isinstance(nv, ast.Constant) and isinstance(nv.value, str):
                name = nv.value
            au = _kw(d, "autouse")
            info = {"name": name or f.name, "autouse": isinstance(au, ast.Constant) and au.value is True}
            sc = _kw(d, "scope")
            if isinstance(sc, ast.Constant) and isinstance(sc.value, str):
                info["scope"] = sc.value
            pv = _kw(d, "params")
            if isinstance(pv, (ast.List, ast.Tuple)):
                info["params"] = {"cases": len(pv.elts), "values": [_text(x, 40) for x in pv.elts[:10]]}
            self.fixture_info[f.id] = info
            self.fixtures_in[self.scope_key(f)][info["name"]] = f
        # fixtures imported into a conftest / test module, and the fixtures of its pytest_plugins modules
        by_id = {f.id: f for f in prog.funcs.values() if f.id in self.fixture_info}
        for rel in self.test_files:
            m = prog.by_file[rel]
            key = ("mod", m.name)
            for local, imp in m.imports.items():
                if imp[0] != "sym":
                    continue
                t = prog._resolve_import(imp)
                if t and t[0] == "func" and t[1].id in by_id and local not in self.fixtures_in[key]:
                    self.fixtures_in[key][self.fixture_info[t[1].id]["name"]] = t[1]
            for val, _ln, _ann in m.vars.get("pytest_plugins", ()):
                for name in _strs(val) or []:
                    pm = prog.module(name)
                    if pm is None:
                        continue
                    for fname, pf in self.fixtures_in.get(("mod", pm.name), {}).items():
                        # a root conftest's plugins are visible everywhere below it
                        self.fixtures_in[key].setdefault(fname, pf)

    @staticmethod
    def scope_key(f) -> tuple:
        return ("cls", f.cls.qual) if f.cls is not None else ("mod", f.module.name)

    def conftest_chain(self, rel: str) -> list[tuple]:
        """Module scope keys of the conftest.py files from rel's directory up to the indexed root."""
        parts = rel.split("/")[:-1]
        out = []
        for i in range(len(parts), -1, -1):
            d = "/".join(parts[:i])
            m = self.prog.by_file.get(f"{d}/conftest.py" if d else "conftest.py")
            if m is not None:
                out.append(("mod", m.name))
        return out

    def scope_chain(self, f_or_cls, module) -> list[tuple]:
        """Fixture lookup order for code in class `c` (or a module function): class and its local bases, the module,
        then the conftest files up the tree."""
        out = []
        c = f_or_cls
        if c is not None:
            out.append(("cls", c.qual))
            out += [("cls", b[1].qual) for b in self.prog.mro(c) if b[0] == "type"]
            o = c.outer
            while o is not None:
                out.append(("cls", o.qual))
                o = o.outer
        out.append(("mod", module.name))
        out += [k for k in self.conftest_chain(module.file) if k != ("mod", module.name)]
        return out

    def lookup_fixture(self, name: str, chain: list[tuple], skip=None):
        for key in chain:
            f = self.fixtures_in.get(key, {}).get(name)
            if f is not None and f is not skip:
                return f
        return None

    @staticmethod
    def requested(fnode, drop=()) -> list[str]:
        """Fixture names a test / fixture function requests by its parameters (no default value)."""
        a = fnode.args
        pos = list(a.posonlyargs) + list(a.args)
        n_def = len(a.defaults)
        names = [x.arg for x in (pos[:len(pos) - n_def] if n_def else pos)]
        names += [x.arg for x, dv in zip(a.kwonlyargs, a.kw_defaults) if dv is None]
        return [n for n in names if n not in ("self", "cls") and n not in drop]

    @staticmethod
    def dynamic_fixtures(fnode) -> list[str]:
        from .plugin import walk_body
        out = []
        for sub in walk_body(fnode):
            if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute) and sub.func.attr == "getfixturevalue" \
                    and sub.args and isinstance(sub.args[0], ast.Constant) and isinstance(sub.args[0].value, str):
                out.append(sub.args[0].value)
        return out

    def usefixtures(self, decorators) -> list[str]:
        out = []
        for d in decorators or ():
            if isinstance(d, ast.Call) and _deco_name(d).split(".")[-1] == "usefixtures":
                out += [x.value for x in d.args if isinstance(x, ast.Constant) and isinstance(x.value, str)]
        return out

    def module_marks(self, m) -> list:
        out = []
        for val, _ln, _ann in m.vars.get("pytestmark", ()):
            out += list(val.elts) if isinstance(val, (ast.List, ast.Tuple)) else [val]
        return out

    def link_fixtures(self, src_id: str, names: list[str], chain: list[tuple], file, line, via="fixture", skip=None) -> int:
        n = 0
        for name in dict.fromkeys(names):
            fx = self.lookup_fixture(name, chain, skip)
            if fx is None:
                continue
            self.b.add_edge(src_id, fx.id, "TEST_USES", file, line, RESOLVED, via=via, fixture=name)
            n += 1
        return n

    def fixture_edges(self) -> int:
        """Fixture -> the fixtures it requests (in its own scope)."""
        n = 0
        prog = self.prog
        for fid, info in self.fixture_info.items():
            f = prog.funcs.get(fid.split(":", 1)[1])
            if f is None:
                continue
            chain = self.scope_chain(f.cls, f.module)
            names = self.requested(f.node, drop=("request",)) + self.usefixtures(f.decorators) + self.dynamic_fixtures(f.node)
            n += self.link_fixtures(f.id, names, chain, f.file, f.line, skip=f)
        return n

    def autouse(self, chain: list[tuple]) -> list:
        seen, out = set(), []
        for key in chain:
            for name, f in self.fixtures_in.get(key, {}).items():
                if name in seen:
                    continue
                seen.add(name)
                if self.fixture_info.get(f.id, {}).get("autouse"):
                    out.append(f)
        return out

    # ------------------------------------------------------------------ test cases
    def is_unittest_class(self, c) -> bool:
        return self.testcase_base(c) is not None

    def testcase_base(self, c) -> str | None:
        """Short name of the nearest `...TestCase` ancestor (unittest / Django / DRF), None for other classes."""
        return next((x.rsplit(".", 1)[-1] for x in self.prog.lineage(c) if x.rsplit(".", 1)[-1].endswith("TestCase")), None)

    def has_init(self, c) -> bool:
        return self.prog.find_method(c, "__init__") is not None

    @staticmethod
    def disabled(c) -> bool:
        v = c.attrs.get("__test__")
        return v is not None and isinstance(v[0], ast.Constant) and v[0].value is False

    def params_of(self, decorators) -> tuple[list, list, list]:
        """(parametrize records, argnames, other mark names) from a decorator list."""
        params, names, marks = [], [], []
        for d in decorators or ():
            dn = _deco_name(d)
            last = dn.split(".")[-1]
            if last == "parametrize" and isinstance(d, ast.Call) and d.args:
                an = _strs(d.args[0]) or []
                names += an
                rec = {"names": an}
                vals = d.args[1] if len(d.args) > 1 else _kw(d, "argvalues")
                if isinstance(vals, (ast.List, ast.Tuple, ast.Set)):
                    rec["cases"] = len(vals.elts)
                    ids = _kw(d, "ids")
                    lits = _strs(ids) if isinstance(ids, (ast.List, ast.Tuple)) else None
                    if lits:
                        rec["ids"] = lits[:20]
                    else:
                        rec["values"] = [_text(x.args[0] if isinstance(x, ast.Call) and _deco_name(x).endswith("param") and len(x.args) == 1
                                               else x, 40) for x in vals.elts[:20]]
                params.append(rec)
            elif ".mark." in f".{dn}" and last not in ("usefixtures", "parametrize"):
                marks.append(last)
        return params, names, marks

    def add_case(self, key: str, name: str, f, framework: str, extra: dict | None = None) -> str:
        from .plugin import module_of
        attrs = {"framework": framework, "test": True, **(extra or {})}
        tid = self.b.add_node("test", key, name=name, fqn=key, file=f.file, line=f.line,
                              end_line=getattr(f.node, "end_lineno", None), module=module_of(f.file), lang="python",
                              entry_kind="test", attrs=attrs)
        self.b.add_edge(tid, f.id, "TEST_CALLS", f.file, f.line, EXACT, via="test method" if f.cls else "test function")
        return tid

    def cases(self) -> dict:
        prog = self.prog
        counts = defaultdict(int)
        st = defaultdict(int)
        for rel in sorted(self.test_files):
            m = prog.by_file[rel]
            disc = self.discovery_for(rel)
            collect = disc.collects(rel) and self.test_files[rel] != "conftest"
            mod_setup = [m.funcs[n] for n in SETUP_MODULE if n in m.funcs]
            mmarks = self.module_marks(m)
            mod_params, mod_argnames, mod_marknames = self.params_of(mmarks)
            mod_use = self.usefixtures(mmarks)
            if collect:
                for name, f in m.funcs.items():
                    if not disc.is_test_function(name) or f.id in self.fixture_info:
                        continue
                    params, argnames, marks = self.params_of(f.decorators)
                    params, argnames, marks = mod_params + params, mod_argnames + argnames, mod_marknames + marks
                    extra = self._extra(params, marks)
                    tid = self.add_case(f.qual, name, f, "pytest", extra=extra)
                    counts["pytest"] += 1
                    st["parametrized"] += bool(params)
                    self._wire(tid, f, None, m, argnames, mod_use, mod_setup, st)
            for c in m.all_classes:
                unit = self.testcase_base(c)
                if not unit:
                    if not collect or not disc.is_test_class(c.name) or self.has_init(c) or self.disabled(c):
                        continue
                    o = c.outer
                    if o is not None and not (disc.is_test_class(o.name) and not self.is_unittest_class(o)):
                        continue
                elif self.disabled(c):
                    continue
                cparams, cnames, cmarks = self.params_of(c.decorators)
                cuse = self.usefixtures(c.decorators)
                klasses = [c] + [b[1] for b in prog.mro(c) if b[0] == "type"]
                setup = []
                for sn in SETUP_METHODS:
                    sf = prog.find_method(c, sn)
                    if sf is not None and sf.file in self.test_files:
                        setup.append(sf)
                seen = set()
                for k in klasses:
                    for mn, f in k.methods.items():
                        if mn in seen:
                            continue
                        seen.add(mn)
                        ok = mn.startswith("test") if unit else disc.is_test_function(mn)
                        if not ok or f.id in self.fixture_info or not isinstance(f.node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                            continue
                        params, argnames, marks = self.params_of(f.decorators)
                        params = mod_params + cparams + params
                        argnames = mod_argnames + cnames + argnames
                        marks = mod_marknames + cmarks + marks
                        extra = self._extra(params, marks)
                        if f.cls is not c:
                            extra["inherited_from"] = f.cls.qual
                        if unit:
                            extra["testcase"] = unit
                        fw = "unittest" if unit else "pytest"
                        rel_name = c.qual[len(m.name) + 1:] if c.qual.startswith(m.name + ".") else c.name
                        tid = self.add_case(f"{c.qual}.{mn}", f"{rel_name}.{mn}", f, fw, extra=extra)
                        # a test node for an inherited method sits at the subclass
                        if f.cls is not c:
                            self.b.nodes[tid].file, self.b.nodes[tid].line = c.file, c.line
                        counts[fw] += 1
                        st["parametrized"] += bool(params)
                        for sf in setup:
                            self.b.add_edge(tid, sf.id, "TEST_CALLS", sf.file, sf.line, RESOLVED, via=sf.name)
                        self._wire(tid, f, c, m, argnames, mod_use + cuse, mod_setup, st)
        return {"cases": dict(counts), **dict(st)}

    @staticmethod
    def _extra(params, marks) -> dict:
        out = {}
        if params:
            out["params"] = params
            n = 1
            for p in params:
                n = n * p["cases"] if isinstance(p.get("cases"), int) and n is not None else None
            if n is not None:
                out["cases"] = n
        if marks:
            out["marks"] = sorted(set(marks))
        return out

    def _wire(self, tid, f, c, m, argnames, use, mod_setup, st) -> None:
        for sf in mod_setup:
            self.b.add_edge(tid, sf.id, "TEST_CALLS", sf.file, sf.line, RESOLVED, via=sf.name)
        if not self.fixture_info:
            return
        key = (c.qual if c is not None else None, m.name)
        cached = self._chains.get(key)
        if cached is None:
            chain = self.scope_chain(c, m)
            cached = self._chains[key] = (chain, self.autouse(chain))
        chain, auto = cached
        fk = f.id
        own = self._own.get(fk)
        if own is None:
            own = self._own[fk] = (self.requested(f.node), self.usefixtures(f.decorators), self.dynamic_fixtures(f.node))
        drop = set(argnames) | {"request"}
        names = [n for n in own[0] if n not in drop] + own[1] + use + own[2]
        st["fixture_uses"] += self.link_fixtures(tid, names, chain, f.file, f.line)
        for fx in auto:
            self.b.add_edge(tid, fx.id, "TEST_USES", fx.file, fx.line, RESOLVED, via="autouse fixture",
                            fixture=self.fixture_info[fx.id]["name"])
            st["autouse_uses"] += 1

    # ------------------------------------------------------------------ HTTP requests
    def is_client(self, recv, ctx, depth=0) -> bool:
        from .plugin import dotted
        if depth > 3:
            return False
        if isinstance(recv, ast.Name) and ctx.func is not None:   # c = APIClient() / with app.test_client() as c
            lv = self.prog.local_vars(ctx).get(recv.id, ())
            for val, _ann, kind in lv:
                if kind in ("assign", "with") and isinstance(val, ast.Call) and self.is_client(val, ctx, depth + 1):
                    return True
            if any(kind == "param" for _v, _a, kind in lv):          # a fixture that returns / yields a client
                fx = self.lookup_fixture(recv.id, self.scope_chain(ctx.func.cls, ctx.func.module), skip=ctx.func)
                if fx is not None and self.fixture_is_client(fx, depth + 1):
                    return True
        d = dotted(recv)
        if d and d.rsplit(".", 1)[-1].lower().endswith("client"):
            return True
        if isinstance(recv, ast.Call):
            fd = dotted(recv.func) or ""
            last = fd.rsplit(".", 1)[-1]
            if last == "test_client" or last.endswith("Client"):
                return True
        try:
            t = self.prog.infer(recv, ctx)
        except RecursionError:
            return False
        if t and t[0] in ("einst", "ext"):
            last = str(t[1]).rsplit(".", 1)[-1]
            return last == "test_client" or last.endswith("Client")
        if t and t[0] == "inst":
            return t[1].name.endswith("Client") or any(x.rsplit(".", 1)[-1].endswith("Client") for x in self.prog.lineage(t[1]))
        return False

    def fixture_is_client(self, fx, depth) -> bool:
        cache = self.__dict__.setdefault("_fx_client", {})
        if fx.id in cache:
            return cache[fx.id]
        cache[fx.id] = False
        from .plugin import walk_body
        ctx = self.Ctx(fx.module, fx, fx.cls)
        cache[fx.id] = any(isinstance(sub, (ast.Return, ast.Yield)) and sub.value is not None
                           and self.is_client(sub.value, ctx, depth) for sub in walk_body(fx.node))
        return cache[fx.id]

    def url_values(self, e, ctx, depth=0) -> list[tuple]:
        """[("path", "/books/{pk}/") | ("name", "shop:book-detail")] for a URL expression; [] when unknown."""
        if e is None or depth > 6:
            return []
        prog = self.prog
        if isinstance(e, ast.Constant) and isinstance(e.value, str):
            return [("path", e.value)]
        if isinstance(e, ast.JoinedStr):
            outs = [""]
            for v in e.values:
                if isinstance(v, ast.Constant):
                    outs = [o + str(v.value) for o in outs]
                    continue
                lits = self.param_values.get(v.value.id) if isinstance(v.value, ast.Name) else None
                if not lits and isinstance(v.value, (ast.Name, ast.Attribute)):
                    sv = str_value(prog, v.value, ctx)        # f"{settings.API_V1_STR}/items/": a constant prefix
                    lits = [sv] if sv and "{" not in sv else None
                outs = [o + x for o in outs for x in lits][:6] if lits else [o + "{" + _ph(v) + "}" for o in outs]
            return [("path", o) for o in outs]
        if isinstance(e, ast.BinOp) and isinstance(e.op, ast.Mod):
            out = []
            for k, v in self.url_values(e.left, ctx, depth + 1):
                if k == "path":
                    out.append(("path", re.sub(r"%\((\w+)\)[sdi]|%[sdi]", lambda mm: "{" + (mm.group(1) or "p") + "}", v)))
            return out
        if isinstance(e, ast.BinOp) and isinstance(e.op, ast.Add):
            left = [v for k, v in self.url_values(e.left, ctx, depth + 1) if k == "path"]
            if not left:
                return []
            right = [v for k, v in self.url_values(e.right, ctx, depth + 1) if k == "path"] or ["{" + _ph(e.right) + "}"]
            return [("path", a + b) for a in left[:3] for b in right[:3]][:3]
        if isinstance(e, ast.Call):
            from .plugin import dotted
            fn = e.func
            last = (dotted(fn) or "").rsplit(".", 1)[-1]
            v = str_value(prog, e.args[0], ctx) if last in URL_NAME_FUNCS and e.args else None
            if v is not None and "{" not in v:
                return [("path", v)] if v.startswith("/") else [("name", v)]
            if isinstance(fn, ast.Attribute) and fn.attr == "format":
                return [("path", re.sub(r"\{(\w*)[^{}]*\}", lambda mm: "{" + (mm.group(1) if mm.group(1) and not mm.group(1).isdigit() else "p") + "}", v))
                        for k, v in self.url_values(fn.value, ctx, depth + 1) if k == "path"]
            return []
        if isinstance(e, ast.Name):
            if ctx.func is not None:
                lv = prog.local_vars(ctx)
                if e.id in lv:
                    out = []
                    for val, _ann, kind in lv[e.id]:
                        if kind == "assign" and val is not None:
                            out += self.url_values(val, ctx, depth + 1)
                    return out[:3]
            r = prog.resolve_name(ctx.mod, e.id)
            if r and r[0] == "var":
                out = []
                for val, _ln, _ann in r[1].vars.get(r[2], ()):
                    out += self.url_values(val, self.Ctx(r[1], None, None), depth + 1)
                return out[:3]
            sv = str_value(prog, e, ctx)        # an imported constant (`from app.urls import ITEMS`)
            return [("path", sv)] if sv is not None else []
        if isinstance(e, ast.Attribute) and not (isinstance(e.value, ast.Name) and e.value.id in ("self", "cls")):
            sv = str_value(prog, e, ctx)        # settings.API_V1_STR, urls.ITEMS
            return [("path", sv)] if sv is not None else []
        if isinstance(e, ast.Attribute) and isinstance(e.value, ast.Name) and e.value.id in ("self", "cls") and ctx.cls is not None:
            c = ctx.cls
            for k in [c] + [b[1] for b in prog.mro(c) if b[0] == "type"]:
                if e.attr in k.attrs and k.attrs[e.attr][0] is not None:
                    return self.url_values(k.attrs[e.attr][0], self.Ctx(k.module, None, k), depth + 1)
                vals = k.self_attrs.get(e.attr)
                if vals:
                    out = []
                    for val, _ann, f2 in vals:
                        if val is not None:
                            out += self.url_values(val, self.Ctx(k.module, f2, k), depth + 1)
                    return out[:3]
            return []
        return []

    @staticmethod
    def literal_params(f) -> dict:
        """argname -> literal str / int values of a single-name `@pytest.mark.parametrize` (URL parts in f-strings)."""
        out = {}
        for d in f.decorators:
            if not (isinstance(d, ast.Call) and _deco_name(d).split(".")[-1] == "parametrize" and len(d.args) > 1):
                continue
            names = _strs(d.args[0]) or []
            vals = d.args[1]
            if len(names) == 1 and isinstance(vals, (ast.List, ast.Tuple)) and vals.elts and \
                    all(isinstance(x, ast.Constant) and isinstance(x.value, (str, int)) and not isinstance(x.value, bool) for x in vals.elts):
                out[names[0]] = [str(x.value) for x in vals.elts[:6]]
        return out

    def collect_http(self) -> None:
        from .plugin import walk_body
        prog = self.prog
        for f in prog.funcs.values():
            if f.file not in self.test_files:
                continue
            ctx = self.Ctx(f.module, f, f.cls)
            self.param_values = self.literal_params(f)
            for sub in walk_body(f.node):
                if not (isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute)):
                    continue
                attr = sub.func.attr
                if attr in HTTP_VERBS:
                    verb, ua = HTTP_VERBS[attr], (sub.args[0] if sub.args else _kw(sub, "path") or _kw(sub, "url"))
                elif attr in GENERIC_VERBS and len(sub.args) >= 2 and isinstance(sub.args[0], ast.Constant) \
                        and isinstance(sub.args[0].value, str):
                    verb, ua = sub.args[0].value.upper(), sub.args[1]
                else:
                    continue
                if ua is None or (isinstance(ua, ast.Constant) and not (
                        isinstance(ua.value, str) and ua.value.startswith(("/", "http://", "https://")))):
                    continue      # d.get("key"), cache.get(1): not a request path
                if not self.is_client(sub.func.value, ctx):
                    continue
                vals = self.url_values(ua, ctx)
                self.pending.append((f, verb, vals, sub.lineno, f"{_text(sub.func, 40)}", self.request_host(sub, ctx, ua)))

    @staticmethod
    def _overrides_in(node) -> list[str]:
        """`app.dependency_overrides[get_current_user] = fake` / `.update({dep: fake})` in a body: the overridden
        dependency names (#59)."""
        from .plugin import dotted
        out = []
        for sub in ast.walk(node):
            keys = []
            if isinstance(sub, ast.Assign):
                for t in sub.targets:
                    if isinstance(t, ast.Subscript) and isinstance(t.value, ast.Attribute) and \
                            t.value.attr == "dependency_overrides":
                        keys.append(t.slice)
            elif isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute) and sub.func.attr == "update" and \
                    isinstance(sub.func.value, ast.Attribute) and sub.func.value.attr == "dependency_overrides" and \
                    sub.args and isinstance(sub.args[0], ast.Dict):
                keys += [k for k in sub.args[0].keys if k is not None]
            for k in keys:
                n = (dotted(k) or "").rsplit(".", 1)[-1]
                if n and n not in out:
                    out.append(n)
        return out

    def overrides_for(self, f) -> list[str]:
        """Dependency overrides in effect for a test: in the test itself, the fixtures it requests (and theirs, two
        levels, conftest included), and its module's top level."""
        cache = self.__dict__.setdefault("_ovr", {})
        if f.id in cache:
            return cache[f.id]
        out = list(self._overrides_in(f.node))
        chain = self.scope_chain(f.cls, f.module)
        todo, seen = [(n, 0) for n in self.requested(f.node)], set()
        while todo:
            n, d = todo.pop()
            fx = self.lookup_fixture(n, chain)
            if fx is None or fx.id in seen or d > 2:
                continue
            seen.add(fx.id)
            out += [x for x in self._overrides_in(fx.node) if x not in out]
            todo += [(m, d + 1) for m in self.requested(fx.node)]
        for st in getattr(f.module, "tree", ast.Module(body=[], type_ignores=[])).body:
            if not isinstance(st, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                out += [x for x in self._overrides_in(st) if x not in out]
        cache[f.id] = out
        return out

    def request_host(self, call, ctx, ua) -> tuple | None:
        """The host a test request names (#59): Flask `subdomain="api"`, `base_url="http://api.example.com"`,
        `headers={"Host": ...}`, or an absolute URL: ("subdomain", "api") / ("host", "api.example.com")."""
        sd = _kw(call, "subdomain")
        v = str_value(self.prog, sd, ctx) if sd is not None else None
        if v:
            return ("subdomain", v)
        hosts = []
        bu = _kw(call, "base_url")
        if bu is not None:
            hosts.append(str_value(self.prog, bu, ctx))
        hd = _kw(call, "headers")
        if isinstance(hd, ast.Dict):
            for k, val in zip(hd.keys, hd.values):
                if isinstance(k, ast.Constant) and str(k.value).lower() == "host":
                    hosts.append(str_value(self.prog, val, ctx))
        if isinstance(ua, ast.Constant) and isinstance(ua.value, str) and "://" in ua.value:
            hosts.append(ua.value)
        for h in hosts:
            if h:
                h = re.sub(r"^\w+://", "", h).split("/")[0]
                h = h if h.startswith("[") else h.split(":")[0]
                if h and not LOCAL_HOSTS.match("http://" + h.split("]:")[0] + ("]" if "]:" in h else "")):
                    return ("host", h)
        return None

    @staticmethod
    def nearest_routes(f, cands: list[dict]) -> set:
        """Same path on several apps (#59): a test that builds its own app and registers routes on it (pallets/flask:
        `@app.route("/")` inside the test, on the `app` fixture) requests those, not the same path another test
        registered. Prefer routes declared in the test function itself, then in its test module."""
        node = getattr(f, "node", None)
        lo, hi = getattr(node, "lineno", 0), getattr(node, "end_lineno", 0) or 0
        same_fn = {r["id"] for r in cands if r.get("file") == f.file and lo <= (r.get("line") or 0) <= hi}
        if same_fn:
            return same_fn
        return {r["id"] for r in cands if r.get("file") == f.file}

    @staticmethod
    def host_fits(route: dict, rh: tuple | None) -> bool:
        if rh is None:
            return not route.get("host") and not route.get("subdomain")
        kind, v = rh
        if kind == "subdomain":
            return route.get("subdomain") == v or (route.get("host") or "").split(".")[0] == v
        return route.get("host") == v or (bool(route.get("subdomain")) and v.split(".")[0] == route["subdomain"])

    def match_http(self) -> dict:
        """TEST_HTTP edges for the requests collect_http() found, against the route nodes in the graph."""
        from ...link import match_endpoint
        b = self.b
        st = defaultdict(int)
        routes, by_name = [], defaultdict(list)
        for nid, n in b.nodes.items():
            a = n.attrs or {}
            if n.kind != "route" or not a.get("uri") or not a.get("method") or a.get("method") == "WS":
                continue
            uris = [("as-declared", a["uri"])]
            if a["uri"].startswith("/{?}") and len(a["uri"]) > 4:
                # a prefix cg could not evaluate (f'{settings.BASE_PATH}...'): tests usually run with it empty
                uris.append(("without unresolved prefix", "/" + a["uri"][4:].lstrip("/")))
            routes.append({"id": nid, "uri": a["uri"], "method": a["method"], "uris": uris, "host": a.get("host"),
                           "subdomain": a.get("subdomain"), "file": n.file, "line": n.line or 0})
            if a.get("name"):
                by_name[a["name"]].append(n)
        unmatched = []
        by_id = {r["id"]: r for r in routes}
        for f, verb, vals, line, via, rhost in self.pending:
            st["requests"] += 1
            if not vals:
                st["url_unknown"] += 1
                continue
            known = [(k, v) for k, v in vals if k == "name" or LOCAL_HOSTS.sub("", v).startswith("/")]
            if not known:
                st["url_unknown"] += 1    # `self.client.get(f"{url}?x=1")` with url from a helper, an external URL
                continue
            hit_any = False
            for kind, v in known:
                if kind == "name":
                    ns, _, nm = v.rpartition(":")
                    cands = [n for n in by_name.get(nm, []) if not ns or (n.attrs.get("namespace") or "") == ns
                             or (n.attrs.get("namespace") or "").endswith(":" + ns)]
                    if not ns and len({n.attrs.get("namespace") for n in cands}) > 1:
                        cands = [n for n in cands if not n.attrs.get("namespace")] or cands
                    hits = [n for n in cands if n.attrs.get("method") in (verb, "ANY") or (verb == "HEAD" and n.attrs.get("method") == "GET")]
                    if len(hits) > 3:
                        hits = []
                    conf = EXACT if len(hits) == 1 else HEURISTIC
                    for n in hits:
                        b.add_edge(f.id, n.id, "TEST_HTTP", f.file, line, conf, via=f"{via}(reverse('{v}'))")
                    hit_any = hit_any or bool(hits)
                    continue
                path = LOCAL_HOSTS.sub("", v.split("?")[0].split("#")[0])
                if not path.startswith("/"):
                    continue     # `client.get("key")` on something that is not an HTTP test client, or an external URL
                if path == "/":       # the site root: a literal in-process request (the client matcher skips "/")
                    res = {"matched": [{"route": r["id"], "confidence": EXACT} for r in routes if r["uri"] in ("/", "")
                                       and (r["method"] in (verb, "ANY") or (verb == "HEAD" and r["method"] == "GET"))]}
                else:
                    res = match_endpoint(verb, path, routes, "api") if routes else {"matched": []}
                if not res["matched"]:
                    # a test that registers an all-parameter rule on its own app and requests it (pallets/flask:
                    # `@app.route("/<list:args>")` + `client.get("/1,2,3")`, `/<lang_code>/` + `/de/`): the
                    # one-literal-segment rule is waived for routes declared in the same test function (#59)
                    from ...link import match_path
                    own = self.nearest_routes(f, routes)
                    loc = [r for r in routes if r["id"] in own and r.get("file") == f.file
                           and (r["method"] in (verb, "ANY") or (verb == "HEAD" and r["method"] == "GET"))
                           and getattr(f.node, "lineno", 0) <= (r.get("line") or 0) <= (getattr(f.node, "end_lineno", 0) or 0)
                           and match_path(path, r["uri"])[0]]
                    if loc:
                        st["own_param_routes"] += 1
                        res = {"matched": [{"route": r["id"], "confidence": HEURISTIC} for r in loc[:3]]}
                fit = [mm for mm in res["matched"] if self.host_fits(by_id.get(mm["route"], {}), rhost)]
                if not fit and rhost is not None:   # a host no host-bound route serves: the default-host routes
                    fit = [mm for mm in res["matched"] if self.host_fits(by_id.get(mm["route"], {}), None)]
                if fit and len(fit) < len(res["matched"]):        # same path on several hosts: the request's host
                    st["host_narrowed"] += 1
                    res["matched"] = fit
                if len(res["matched"]) > 1:
                    near = self.nearest_routes(f, [by_id.get(mm["route"], {}) for mm in res["matched"]])
                    if near and len(near) < len(res["matched"]):
                        st["app_narrowed"] += 1
                        res["matched"] = [mm for mm in res["matched"] if mm["route"] in near]
                if len(res["matched"]) > 3 and all(mm["confidence"] == HEURISTIC for mm in res["matched"]):
                    res["matched"] = []
                ovr = self.overrides_for(f) if res["matched"] else []
                for mm in res["matched"]:
                    b.add_edge(f.id, mm["route"], "TEST_HTTP", f.file, line, mm["confidence"], via=via, path=path,
                               **({"dependency_overrides": ovr} if ovr else {}))
                if ovr:
                    st["with_dependency_overrides"] += 1
                hit_any = hit_any or bool(res["matched"])
            if hit_any:
                st["matched"] += 1
            else:
                st["unmatched"] += 1
                if len(unmatched) < 5:
                    unmatched.append(f"{f.file}:{line} {verb} {vals[0][1]}")
        out = dict(st)
        if not routes and st["requests"]:
            out["note"] = "no route nodes in the graph (web framework without a cg plugin): requests stay unlinked"
        if unmatched:
            out["unmatched_samples"] = unmatched
        self.pending = []
        return out

    # ------------------------------------------------------------------ driver
    def index(self) -> dict:
        self.classify()
        if not self.test_files:
            return {}
        self.collect_fixtures()
        marked = self.mark()
        res = self.cases()
        fx_edges = self.fixture_edges()
        self.collect_http()
        why = defaultdict(int)
        for v in self.test_files.values():
            why[v] += 1
        st = {"test_files": dict(why), "test_code_nodes": marked, **res, "fixtures": len(self.fixture_info),
              "fixture_chain_edges": fx_edges}
        cfgs = [d.as_dict() for d in self.discs if d.source]
        if cfgs:
            st["pytest_config"] = cfgs
        self.st = st
        return st
