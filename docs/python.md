# Python: source roots, entry points and tests

cg names every Python module the way the interpreter imports it, so `from core.policy import is_allowed` lands on
`lib/core/policy.py` whether the project uses a flat layout, a `src/` layout, a `lib/` directory put on `sys.path` by
a launcher, several packages side by side, namespace packages or nested projects. The Python plugin runs whenever the
repository has `.py` files outside dependency, build and cache directories; no marker file or flag is needed.

## Detection

A **source root** is a directory whose contents are importable by top-level name. cg collects them from this
evidence, and records the reason for each:

| evidence | example | reason shown |
|---|---|---|
| the indexed root | `app/views.py` -> `app.views` | `indexed root` |
| packaging config at the root or in a nested project | `pyproject.toml`: `[tool.setuptools] package-dir = {"" = "code"}`, `[tool.setuptools.packages.find] where`, `[tool.hatch.build(.targets.*)] packages / sources`, `[tool.poetry] packages = [{from = ...}]`, `[tool.pdm.build] package-dir`, `[tool.maturin] python-source`; `setup.cfg` `[options] package_dir`, `[options.packages.find] where`; `setup.py` literal `package_dir=` / `find_packages("src")` | `pyproject.toml [tool.setuptools] package-dir` |
| conventional directories that are not packages themselves | `src/`, `lib/`, `python/` (at the root and in nested projects) | `lib/ directory` |
| nested projects (depth 4 or less) | a directory holding `pyproject.toml`, `setup.py`, `setup.cfg` or `manage.py` | `nested project (backend/manage.py)` |
| namespace packages (PEP 420) | `plugins/acme/core/base.py` with no `__init__.py`, imported as `acme.core.base` -> `plugins/` | `namespace package acme (imported as acme.core)` |
| the parent of every top-level package | `services/billing/billing/__init__.py` -> `services/billing/` | `parent of top-level package billing` |

A `package-dir` entry that maps a package to another directory (`{"core" = "lib"}`) makes `lib/` hold package `core`.

## Module names

A file can be importable under several roots (`src/acme/api.py` is `acme.api` under `src/` and `src.acme.api` under
the indexed root). Its node gets one canonical name, chosen by the project's own imports: the root whose top-level name
the imports use most wins, and on a tie the deepest root. The other names stay aliases, so imports written either way
resolve to the same node. `cg coverage` reports the modules where the imports used more than one name.

Two package trees can claim the same name, typically `tests/` in each project of a monorepo. The tree with the most
import evidence keeps the name (then a tree without any other importable name, then the shallower root); the others
are named by their path from the indexed root (`svc-b.tests.conftest`), which keeps them in the graph with their
calls, and keeps relative imports inside each tree resolving. Coverage lists how many files were renamed this way.

Files whose path is not an importable name (`my-scripts/run.py`, `alembic/versions/1a2b_add.py`) are listed in the
`unmapped` bucket ([completeness.md](completeness.md)).

## Configuration

Set the roots once in the project config file, `.cg.yaml` (or `.cg.yml`) at the indexed root. Configured roots replace
detection; `.` is the indexed root:

```yaml
version: 1
python:
  source_roots: [lib, tools/scripts]
```

`cg index ROOT --python-root lib --python-root tools/scripts` does the same for one run and takes precedence over the
file. The MCP `index` tool re-reads `.cg.yaml` on every re-index and keeps roots given with `--python-root`.
Paths must stay inside the indexed root. A file cg cannot use stops the index with a message naming the file and key
(`.cg.yaml: python.source_roots[0]: '../x' must stay inside the indexed root`); top-level keys this version does not
read are kept and listed in the index stats (`stats.config.ignored_keys`).

## Coverage output

`cg coverage` and the MCP `coverage` tool list the roots with their origin and the number of modules each holds, and
warn about configured roots that do not exist and files outside every root:

```console
$ cg coverage --db out/proj.db
coverage proj: python 3 exact
  python source roots: lib/ (detected: lib/ directory; parent of top-level package core, 3 modules)
every source file cg found is indexed; edges still carry their own exact / resolved / heuristic label.
```

With configured roots, files outside them are `unmapped`, with a hint naming the setting:

```console
coverage conf: not fully covered: python 5 discovered, 4 indexed (exact parser): 1 unmapped
  python source roots: lib/ (configured: configured in .cg.yaml, 2 modules); vendored/ (configured: configured in .cg.yaml, 2 modules)
  python: 5 files (.py 5) 4 indexed, 1 unmapped
    unmapped: scripts/release.py
    fix: unmapped .py files are outside the configured source roots (lib/, vendored/): add their directories to python.source_roots in .cg.yaml, or drop the setting to use detection
```

A layout that only uses the indexed root keeps the short output (`--all-files` shows the root too). In the index stats
the Python plugin records `roots_mode` (`detected`, `configured`, `flag`), `source_roots` (`path`, `origin`, `why`,
`modules`, and `package` for a mapped directory), `roots_warnings`, `roots_ambiguous` and `module_name_collisions`;
MCP `coverage(json_output=true)` adds them as `python_source_roots`.

## Entry points and function references

A Python function is often reached without a direct call: it is listed in a dispatch table, passed as a callback,
registered by a decorator, run from a `__main__` block, or named in packaging metadata. cg records these, so `impact`
reaches the code that runs it.

**Function references.** A function used as a value gets a `REFERENCES_FN` edge from the function (or module, or
class) that mentions it, with `how`:

| `how` | example |
|---|---|
| `collection` | `CHECKS = (check_size, check_owner)`, `HANDLERS = {"create": on_create}`, `registry.append(fn)`, `.add`, `.insert`, `.extend`, `.setdefault` |
| `callback` | `executor.submit(job)`, `sorted(items, key=by_name)`, `Thread(target=worker)`, `signal.connect(receiver)` |
| `assignment` | `handler = on_create`, `self.hook = fallback` |
| `decorator` | `@retry` on a local decorator `retry`: the decorator references the function it wraps |

Properties are attribute reads, not references. A call through the table (`for check in CHECKS: check(item)`,
`HANDLERS[kind](e)`, `self.plugins[name].run()`) becomes a `CALLS` edge with `via="collection"` to each function the
table can hold, at `resolved` confidence; it replaces the unique-name guess cg made before. The table's elements are
followed through copies and the functions that build it: `copy.copy()` / `copy.deepcopy()` / `.copy()`, `list()` /
`sorted()`, filtering comprehensions, a project function that returns the collection, and a constant key or index of
a dict or tuple it returns (`plan = setup(); for p in plan["plugins"]: p.index()`), also when these steps chain
(a deep copy of a module list, filtered in a setup helper, returned in a dict and filtered again inside the loop that
calls it). A call on the items of a list of instances goes to the method each item's class has (inherited or
overridden), and `impact` on an override or on the base lists the loop. Items of an unknown type add no edge.
A local decorator also
gets `CALLS owner -> decorator` with `via="decorator"`. Function-local imports (`from . import routes as R` inside a
def) resolve per function.

**Entry points.** cg adds `script` nodes for code that a command line starts:

| source | node | entry kind | label |
|---|---|---|---|
| `if __name__ == "__main__":` block | `script:<module>` | `main` | `python -m pkg.mod` or `python file.py` |
| `pkg/__main__.py` (whole module body) | `script:pkg.__main__` | `main` | `python -m pkg` |
| `[project.scripts]` / `[project.gui-scripts]`, `[tool.poetry.scripts]`, `[tool.flit.scripts]`, setup.cfg `[options.entry_points] console_scripts`, setup.py literal `entry_points` | `script:console_scripts:<name>` | `main` | `console script <name>` / `GUI script <name>` |
| other entry-point groups (`[project.entry-points.<group>]`, Poetry plugins, setup.cfg / setup.py groups) | `script:<group>:<name>` | `public_api` | `<group> <name>` |

A target `pkg.mod:func` gets `CALLS via="entry_point"` to the function; `pkg.mod:Class` references the class's methods;
a module target references its public functions; a typer or click object runs its registered commands. Targets cg
cannot find are counted in `entry_points_unresolved`, with samples.

Functions registered with a framework are entry points themselves: MCP tools, resources and prompts (`FastMCP`,
`@mcp.tool()`, `mcp.tool()(fn)`, `add_tool`) get `message_handler`; click, asyncclick, rich-click, cloup and typer
commands and callbacks, and Flask `@app.cli.command()` get `cli_command`. A local wrapper that registers the function
it decorates passes the entry on to every function it wraps. An external decorator that registers with a receiver
(`@app.route`, `@bus.subscribe`, ...) gives a `heuristic` reference from the module, marked `registry="external"`.
References into views that Django already wires through `urls.py` are dropped in favour of the route edges.

```console
$ cg impact checks.check_size --db out/checks.db
targets: ['function:checks.check_size']
callers (transitive): 5
  d=1 [module] checks  (ref: collection)
  d=1 [function] checks.validate  (call through a collection)
  d=2 [function] checks.main
  d=3 [script] console script checks
  d=3 [script] python checks.py
entry points: 2
  main             console script checks  conf=resolved
        path: script:console_scripts:checks
          -CALLS[exact @ pyproject.toml:6]-> function:checks.main
          -CALLS[exact @ checks.py:13]-> function:checks.validate
          -CALLS[resolved @ checks.py:10]-> function:checks.check_size
  main             python checks.py  conf=resolved
  ...
```

The Python plugin stats record `references`, `decorator_calls`, `script_entries`, `registrations`,
`entry_points_unresolved` and `references_superseded`.

Not modelled: module-level code that runs on import is not an entry point of its own; property reads are not `CALLS`;
`getattr(obj, name)()` with a computed name and functions registered by external libraries without a receiver stay
unresolved. Test code never counts as a caller: see [Tests](#tests-pytest-and-unittest).

Measured on the same shallow clones as below, before and after:

| Project | REFERENCES_FN edges | CALLS edges | script nodes | entry points added |
|---|---|---|---|---|
| pallets/flask | 0 -> 163 | 1,396 -> 1,538 | 0 -> 3 | 12 `cli_command`, 3 `main` |
| pytest-dev/pytest | 0 -> 386 | 14,589 -> 14,789 | 0 -> 12 | 12 `main` (8 `__main__` blocks, 2 `__main__.py`, 2 console scripts) |
| ansible/ansible | 0 -> 1,035 | 21,772 -> 22,247 | 0 -> 234 | 234 `main` |
| open-telemetry/opentelemetry-python | 0 -> 363 | 11,627 -> 11,723 | 0 -> 64 | 12 `main`, 52 `public_api` (entry-point groups) |
| netbox-community/netbox | 0 -> 192 | 20,439 -> 20,744 | 0 -> 8 | 8 `main`; Django routes and entry points unchanged |
| fastapi/full-stack-fastapi-template | 0 -> 29 | 180 -> 182 | 0 -> 3 | 3 typer `cli_command`, 3 `main` |

Calls through a collection: pytest 10, ansible 79, opentelemetry-python 25, netbox 260. Django-derived edges are
identical before and after (27,897 in netbox). In opentelemetry-python the function-local import fix moved
`_create_otlp_grpc_*_exporter` from the HTTP exporter class to the gRPC one. The Python plugin takes about 5-10% longer
(ansible 11.5 s -> 12.4 s, netbox 13.9 s -> 14.8 s, best of three on a shared machine).

## Stored attributes (`cg readers` / `cg writers`)

These are `field:<Class>.<attr>` nodes (#88), with attrs `property: stored` and `declared: self | class`:
- a class's instance attributes, i.e. `self.x = ...` in any of its methods;
- its annotated class-level attributes, such as dataclass, pydantic or attrs fields. `ClassVar` / `Final` /
  `InitVar` are excluded.

Plain class-level assignments are not fields: they are constants or descriptors, and Django model fields are the
Django plugin's own `field:` nodes. Methods, properties and nested classes are never fields.

An access is a `READS_PROP` / `WRITES_PROP` edge (`resolved`, attr `receiver`) when the type inference knows the
receiver's class and that class (or a base) declares the field. The receiver can be `self`, an annotated parameter,
a local `c = Cart()` or a typed attribute (`self.app.name`). An unknown receiver binds nothing.

Writes are:
- assignment, augmented assignment and `del` targets;
- `self.items.append(x)` and the other in-place list / dict / set / deque methods (`via: mutating`);
- item assignment or deletion, as in `self.cache[k] = v` (`via: item`).

Test code's accesses are `TEST_USES`. Corpus results (call edges and all other edges unchanged):

| corpus | fields | reads | writes |
|---|---|---|---|
| flask | 119 | 290 | 130 |
| beets | 1,558 | 3,467 | 1,016 |
| openai-agents-python | 5,030 | 9,785 | 2,822 |

## Tests (pytest and unittest)

pytest and unittest suites are indexed as test cases, so `cg tests <symbol>` lists the tests that exercise a function
and `impact` / `callers` / `reaches` show only application callers. Test files are found the way pytest finds them,
including the `python_files`, `python_classes`, `python_functions` and `testpaths` settings of `pytest.ini`,
`pyproject.toml`, `tox.ini` or `setup.cfg`; fixtures are followed through `conftest.py` chains, `autouse`,
`usefixtures`, fixture-to-fixture requests and `pytest_plugins`; Django test clients with `reverse()`, DRF `APIClient`
and `APITestCase` requests link to the routes they hit. The full rules are in
[channels-and-tests.md](channels-and-tests.md#tests).

```text
$ cg tests checks.check_size --db out/graph.db
targets: 1 node(s): checks.check_size
tests: 1 direct, 1 transitive (of 2 test cases in the graph: pytest 2)

== DIRECT (the test code itself calls / requests the target): 1
  test_check_size_rejects_big_items  [pytest] tests/test_checks.py:8  depth=2 conf=exact
      test:tests.test_checks.test_check_size_rejects_big_items -TEST_CALLS-> tests.test_checks.test_check_size_rejects_big_items -TEST_CALLS-> checks.check_size

== TRANSITIVE (through application code): 1
  test_validate_accepts_small_items  [pytest] tests/test_checks.py:4  depth=3 conf=exact
      test:tests.test_checks.test_validate_accepts_small_items -TEST_CALLS-> tests.test_checks.test_validate_accepts_small_items -TEST_CALLS-> checks.validate -CALLS-> checks.check_size
```

**Tests that run a program.** A test that starts the project's own program in a subprocess gets `TEST_CALLS`
(`via: subprocess`) to the entry point, so `cg tests` counts it for everything the program reaches:

```python
def test_cli_index(tmp_path):
    subprocess.run([sys.executable, "-m", "pkg.cli", "index", str(tmp_path)])   # -> script:pkg.cli

def run_cli(*args):                                    # a helper with the program fixed: CALLS -> script:pkg.cli,
    return subprocess.run([sys.executable, "-m", "pkg.cli", *args])            # every test calling it reaches it

def run(module, *args):                                # the program is a parameter: each call site is evaluated
    return subprocess.run([sys.executable, "-X", "dev", "-m", module, *args])
```

`-m` modules, `-c` snippets (what they call), script paths and the console scripts the packaging metadata declares
are recognised, also as `shutil.which("tool")` and shell strings; helpers are followed through up to 5 calls.
Argument lists built step by step (`cmd.append(...)`, `cmd += [...]`, `cmd.extend(...)`, `cmd.insert(0, ...)`, an
append of the loop variable in a `for` over a known list) are applied in order; `__file__`, `mod.__file__`,
`os.path.dirname()` and `Path(...).parent` evaluate to project paths. Installed runners whose program is a
parameter come from a table: `scripttest.TestFileEnvironment.run` (pip's `script.pip(...)` through its subclass),
pytest's `Pytester.run` (the `pytester` / `testdir` fixtures), `sh.tool(...)` / `sh.Command("tool")(...)` and plumbum
`local["tool"][...]()`. `python -c "import pkg.plugin"` (no project calls) links to the imported module; a script the
function first copies from a project file or a template (`shutil.copyfile(".../manage.py-tpl", tmp / "manage.py")`)
links to the source, a template being read as Python for what it calls (`how: copied template`). Programs
outside the project (`git`, `-m pip`) add nothing. The Python plugin stats carry a `subprocess` block: process starts,
runners (helpers whose program is a parameter), `linked` by kind, `outside_project` and `unresolved`, with samples.
Rules: [channels-and-tests.md](channels-and-tests.md#tests).

| Project | subprocess runs linked | tests with a path to an entry point | index time |
|---|---|---|---|
| pytest-dev/pytest | 12 (11 `-m pytest` through `Pytester.run` / `popen`, 1 console script) | 0 -> 1,260 of 3,512 | 5.6 s -> 5.5 s |
| pylint-dev/pylint | 13 (`-m pylint`) | 0 -> 10 of 737 | 5.6 s -> 5.5 s |
| django/django | 5 (`-m django`, 2 through `AdminScriptTestCase.run_test`) | 0 -> 100 of 19,832 | 61.1 s -> 63.3 s |
| mkdocs/mkdocs | 2 (`mkdocs build` from its integration script) | - (no test cases) | 1.2 s -> 1.2 s |
| httpie, flake8 | 0 (their tests call the CLI in process; subprocess runs start `git`, `pyinstaller`, ...) | unchanged | unchanged |
| pypa/pip (#60: `scripttest` runner) | 8 -> 15 | 6 -> 828 of 1,939 | within noise (9.7 s / 9.2 s) |
| django/django (#60: copied `manage.py-tpl`, `runtests.py` re-running itself) | 5 -> 9 | 100 -> 218 of 19,837 | within noise (75 s / 71 s) |
| pytest / pylint / httpie (#60) | 12 -> 13 / 13 -> 13 / 0 -> 1 | 1,260 -> 1,261 / 10 -> 10 / 0 -> 4 | unchanged |

In pytest, 102 tests call `pytester.runpytest_subprocess()` (which runs `python -mpytest`) and 10 run `-m pytest`
themselves; the other 1,148 reach it through `pytester.runpytest()`, which runs in a subprocess under
`--runpytest=subprocess`. In Django the admin-script tests reach `django.__main__` through `run_django_admin()`;
`run_manage()` runs a `./manage.py` the test copies into a temporary directory from
`django/conf/project_template/manage.py-tpl`; since #60 that copy links to `execute_from_command_line`, which the
template calls. In pip, `PipTestEnvironment.pip()` runs the `pip` console script through scripttest's
`TestFileEnvironment.run`, so the 828 tests that use the `script` fixture reach pip's CLI. No other edge changed in
any of these repositories (#60: 0 edges lost on pytest, pylint, pip, Django, httpie, mkdocs, flake8).

`cg coverage` adds a `python tests:` line (cases per framework, test files, fixtures, HTTP test requests and how many
reached a route), and the Python plugin stats carry the same under `tests`.

Measured on the same shallow clones as below, before and after:

| Project | test cases | CALLS edges (now application code only) | TEST_* edges | fixtures / fixture uses | HTTP test requests |
|---|---|---|---|---|---|
| pytest-dev/pytest | 0 -> 3,512 (pytest 3,501, unittest 11) | 14,789 -> 2,987 | 0 -> 33,591 | 164 / 2,489 | - |
| pallets/flask | 0 -> 391 | 1,538 -> 430 | 0 -> 3,151 | 23 / 535 | 331 found, 223 linked to Flask routes (see [Web routes](#web-routes-fastapi-starlette-flask)) |
| fastapi/full-stack-fastapi-template | 0 -> 58 | 182 -> 54 | 0 -> 435 | 4 / 123 | 53 found, 53 linked to FastAPI routes |
| open-telemetry/opentelemetry-python | 0 -> 2,599 (pytest 192, unittest 2,407) | 11,723 -> 2,860 | 0 -> 19,793 | 36 / 98 | - |
| ansible/ansible | 0 -> 2,628 (pytest 1,647, unittest 981) | 22,247 -> 14,843 | 0 -> 16,723 | 122 / 845 | - |
| netbox-community/netbox | 0 -> 14,103 (unittest) | 20,744 -> 5,170 | 0 -> 94,514 | - | 1,005 found, 443 linked to Django / DRF routes |

pytest's own `testpaths = testing` and `python_files = ... testing/python/*.py` settings are honoured. In netbox, 10
TEST_HTTP edges sampled at random all land on the route the test requests (DRF router names, `app_name` namespaces);
the requests left unlinked build their URL in a helper method (`self._get_url('list')`) or name routes registered
at run time. Indexing takes about 3-7% longer
(ansible 11.2 s -> 12.0 s, netbox 33.0 s -> 34.1 s, best of three on a shared machine).

## Web routes (FastAPI, Starlette, Flask)

A project that depends on or imports `fastapi` / `starlette` or `flask` gets route nodes (`route:GET /api/v1/items/{id}`)
with a `ROUTES_TO` edge to the handler, built from the app / router / blueprint objects the code assigns
(`app = FastAPI()`, `router = APIRouter(prefix="/items")`, `bp = Blueprint("auth", __name__, url_prefix="/auth")`,
inside a function too, as in Flask's `create_app()` factory):

- FastAPI: `@router.get/post/...`, `@router.api_route(methods=[...])`, `router.add_api_route()`, `@app.websocket`,
  `include_router(router, prefix=...)` chains across modules, router `prefix=` and `dependencies=`. Prefixes and paths
  are evaluated from literals, f-strings, module constants and settings attributes (`settings.API_V1_STR` with
  `class Settings: API_V1_STR: str = "/api/v1"`); a part cg cannot evaluate becomes `{?}`.
- Starlette: `Starlette(routes=[Route(...), Mount("/x", routes=[...]), WebSocketRoute(...)])`, `Router`, `add_route`,
  `HTTPEndpoint` classes (one route per method the class defines). `{id:int}` becomes `{id}`.
- Flask: `@bp.route(..., methods=...)`, `@bp.get` / `.post` / ..., `add_url_rule()` (also `MethodView.as_view()`),
  `register_blueprint(bp, url_prefix=...)` (the registration's prefix replaces the blueprint's own), nested blueprints,
  `<int:id>` → `{id}`. Route names are the endpoints `url_for()` takes (`auth.login`). A multi-segment converter
  (`<path:p>`, Starlette `{p:path}`) becomes `{p*}`, which matches any number of segments.
- Route access (`cg routes --unguarded`): `Depends()` / `Security()` dependencies of the handler (parameter defaults,
  `Annotated[..., Depends(f)]` aliases such as `CurrentUser`), of `dependencies=` on the route, router or
  `include_router`, and the non-framework decorators of a Flask view (`@login_required`). Dependencies built by
  calling a class (`Depends(RoleChecker("admin"))`) are read from the class's `__call__`. `app.add_middleware(X)`
  adds an access entry with `via: middleware` to every route of that app when `X` is a project class (its
  `dispatch` / `__call__` is read for checks) or a library class named like access control (`auth`, `session`,
  `permission`, `jwt`, `token`, `login`); CORS, GZip and other library middleware are skipped.
- What a dependency checks (`attrs.access[].checks`), read from its source: the statuses it raises
  (`HTTPException(status_code=401)`, `status.HTTP_403_FORBIDDEN`, an exception held in a variable), the security
  scheme it is (`OAuth2PasswordBearer`, `HTTPBearer`, `APIKeyHeader` ...; `auto_error=False` does not reject), the
  headers / cookies / request it reads, and its own nested dependencies (followed 4 levels). `effect` is `rejects`
  (401 / 403 here or in a nested dependency, `rejects_via` names it), `raises` (other statuses) or `reads`. A
  dependency that rejects counts as an auth guard whatever its name (`auth_by: dependency check (...)`). Statuses
  raised in a project helper the dependency calls count too (`checks.checked_in` names the helper, and `auth_by`
  ends in `in <helper>`).
- Objects that are not assigned in the module: an app / router received as a parameter
  (`def register_routes(app): @app.get(...)`, `def init_app(app): app.add_url_rule(...)`) hangs under the app passed
  to it (`register_routes(app)`), or under the object the pytest fixture of that name returns
  (`def test_x(app, client): @app.route("/more")`); `app = create_app()` and `app.mount("/admin", make_admin())` use
  the object the function returns; a Flask subclass defined inside a function is an app factory too.
- Class-based routers and views: fastapi-utils / fastapi-restful `@cbv(router)` with `InferringRouter`,
  classy-fastapi `Routable` (`@get("/x")` methods, `Items().router`), flask-restful `Api(app | bp).add_resource()`
  and flask-restx `Api` / `api.namespace()` / `Namespace` with `@ns.route` on `Resource` classes (one route per HTTP
  method the class defines), Flask `View` / `MethodView` `methods = [...]` (a `View` routes to `dispatch_request`).
- Flask rules: `add_url_rule("/", endpoint="index")` without a view takes the view from `@app.endpoint("index")`,
  `app.view_functions["index"] = f` or another rule with that endpoint (`endpoint_alias: true`); werkzeug
  `app.url_map.add(Rule(...) | Submount(...))` likewise; `subdomain=`, `host=` and `defaults=` are route attributes;
  a blueprint registered twice (`name=`, another `url_prefix`) gets both sets of routes; every app has the built-in
  static route `GET /static/{filename}` (`static_url_path=`, none with `static_folder=None`; blueprints with
  `static_folder=` under their prefix), marked `static: true`.
- Starlette `Host("api.example.com", routes=[...] | app=...)` and `app.host(...)`: the host is the route's `host`
  attribute, not part of its path.
- Host-aware route keys: a route with a `host` or `subdomain` gets ` @<host>` (`route:GET / @api.example.org`) or
  ` @<subdomain>.*` (`route:GET / @api.*`) in its node id, so routes with the same method and path on different
  hosts are separate nodes. The route name stays `GET /`.
- Flask-Classful `FlaskView` subclasses registered with `X.register(app | bp, route_base=, route_prefix=)`: `index`,
  `get`, `post`, `put`, `patch` and `delete` follow Flask-Classful's rules, other public methods route under their
  name, and `@route(...)` overrides apply.
- Registration calls inside a `for` loop over a literal list or tuple
  (`for path, view in [("/a", a), ("/b", b)]: app.add_url_rule(path, view_func=view)`) are unrolled, one
  registration per element.
- A router or blueprint no app includes still gets its routes, with `mounted: false` and no entry point.

The `python_decorator_routes` blind spot no longer fires for these frameworks. Test requests through `TestClient(app)`,
`app.test_client()` and fixtures returning one link to the routes (`TEST_HTTP`), so `cg tests <handler>` lists them.
When several routes fit a request, cg narrows them in this order before dropping a request with more than 3
candidates:

- by host: the request's host from `subdomain=`, `base_url=`, a `Host` header or an absolute URL, matched against the
  route's host / subdomain (routes without one stay candidates when no hosted route fits);
- by app: routes registered in the same test function, then in the same test module;
- a route made only of parameters (`/<a>/<b>`) needs one literal segment to match, except when the same test
  function registers it.

A test that sets `app.dependency_overrides[dep] = fake` (in the test, in a fixture it uses, two levels deep, or at
module level) gets `dependency_overrides: ["dep"]` on its `TEST_HTTP` edges, which marks the link as a test that
bypasses that dependency.

Not modelled: view registries built by a plugin system (pluggy-style hooks that return views), because they have no
common API to read, and routes added by calling an unknown library function with the app.

Measured on shallow clones (before -> after):

| Project | routes | HTTP test requests linked | `python_decorator_routes` blind spots | index time |
|---|---|---|---|---|
| pallets/flask `examples/tutorial` | 0 -> 12 | 0 / 21 -> 18 / 21 (3 URLs unknown) | 7 -> 0 | 0.05 s -> 0.06 s |
| fastapi/full-stack-fastapi-template `backend` | 0 -> 23 | 0 / 53 -> 53 / 53 | 23 -> 0 | 0.13 s -> 0.2 s |
| pallets/flask (whole repo, apps built inside its own tests) | 0 -> 156 | 0 / 331 -> 223 / 331 | 13 -> 0 | 0.6 s -> 0.9 s |
| pallets/flask, routes on received apps / fixtures, endpoints, static routes (#48) | 156 -> 443 | 223 / 331 -> 305 / 331 | 0 -> 0 | 1.0 s -> 1.4 s |
| pallets/flask, host-aware keys, per-app test linking, `{p*}`, loops (#59; route **nodes**) | 188 -> 200 | 305 / 331 -> 310 / 331 | 0 -> 0 | 1.9 s -> 1.8 s (wall, whole `cg index`) |

In the template, 17 of 23 routes carry an auth dependency (`CurrentUser`, `get_current_active_superuser`); the 6 without
are login, signup, password recovery, the health check and the private user route. The template's `deps.py` uses
Python 3.14's unparenthesized `except A, B:`; cg re-parses such files with the parentheses added, so it indexes on
older interpreters too. In the flask repository most unlinked requests targeted routes a test registers on the `app`
fixture it receives as a parameter (`def test_x(app, client): @app.route(...)`); with those modelled (186 fixture
parameters), 305 of 331 requests link. Of the 93 new links, 68 land on a route the same test registers, 5 on the
built-in static route and 20 on a same-path route registered elsewhere (route nodes were keyed by method and path).
With #59, routes on other hosts or subdomains are separate nodes (188 -> 200 route nodes, where the #48 row counts
route entries), requests narrow by host (140 requests) and by the test's own app (11), and requests that fit more
than one route fall from 18 to 7 (`TEST_HTTP` edges 323 -> 319, no request lost its link). Newly linked: `/de/`
and `/1,2,3` (all-parameter routes the test registers itself), `/admin/static/css/test.css` (multi-segment static
path) and a `{path*}` catch-all; 21 requests stay unlinked. #59 leaves the FastAPI template and opentelemetry-python
unchanged (same route attributes and edges). Before #59, the FastAPI template,
the tutorial example (+1 node, its static route), opentelemetry-python (+1, a Flask test app's static route), pylint
and pytest index as before; in the template the 17 guarded routes now also carry what their dependencies check
(`get_current_user` rejects through `reusable_oauth2`, an `OAuth2PasswordBearer`).

## Validation

Measured on shallow clones of public projects (default branch, October 2026), before and after source-root detection:

| Project | Layout | Python files indexed | IMPORTS edges | CALLS edges | Python plugin time |
|---|---|---|---|---|---|
| fastapi/full-stack-fastapi-template | nested project (`backend/pyproject.toml`, package `app`) | 40 / 43 -> 40 / 43 | 0 -> 70 | 22 -> 180 | 0.1 s -> 0.1 s |
| open-telemetry/opentelemetry-python | monorepo of ~40 hatch projects, `src/` layouts, `opentelemetry` namespace package | 56 / 758 -> 741 / 758 | 1 -> 2,294 | 69 -> 11,627 | 0.8 s -> 3.7 s |
| ansible/ansible | `lib/` layout (`[tool.setuptools.packages.find] where`), `ansible_collections` namespace packages in tests | 1,564 / 1,839 -> 1,690 / 1,839 | 925 -> 4,440 | 9,935 -> 21,772 | 9.4 s -> 9.1 s |
| pallets/flask | `src/` layout, example apps as nested projects | 83 / 83 -> 83 / 83 | 299 -> 315 | 1,379 -> 1,396 | 0.4 s -> 0.5 s |
| pytest-dev/pytest | `src/` layout with two packages (`_pytest`, `pytest`) | 270 / 274 -> 270 / 274 | 1,473 -> 1,473 | 14,589 -> 14,589 (same graph) | 4.2 s -> 4.0 s |
| netbox-community/netbox | Django project in `netbox/` (`manage.py`) | 980 / 1,290 -> 980 / 1,290 | 4,346 -> 4,346 | 20,439 -> 20,439 (same graph) | 13.4 s -> 13.1 s |

Spot check: 21 IMPORTS edges sampled at random from the new graphs of ansible, opentelemetry-python and the FastAPI
template, compared by hand with the import line they cite: 21/21 land on the right file. The files that stay out are
paths that are not importable names (`hacking/test-module.py`, `docs/examples/fork-process-model/...`, Alembic
revisions such as `versions/1a31ce608336_add_cascade.py`); netbox's 310 are its migrations (excluded by default).
