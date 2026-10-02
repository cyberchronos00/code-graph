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
a dict or tuple it returns (`plan = setup(); for p in plan["plugins"]: p.index()`). A local decorator also
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
  `<int:id>` → `{id}`. Route names are the endpoints `url_for()` takes (`auth.login`).
- Route access (`cg routes --unguarded`): `Depends()` / `Security()` dependencies of the handler (parameter defaults,
  `Annotated[..., Depends(f)]` aliases such as `CurrentUser`), of `dependencies=` on the route, router or
  `include_router`, and the non-framework decorators of a Flask view (`@login_required`).
- A router or blueprint no app includes still gets its routes, with `mounted: false` and no entry point.

The `python_decorator_routes` blind spot no longer fires for these frameworks. Test requests through `TestClient(app)`,
`app.test_client()` and fixtures returning one link to the routes (`TEST_HTTP`), so `cg tests <handler>` lists them.

Measured on shallow clones (before -> after):

| Project | routes | HTTP test requests linked | `python_decorator_routes` blind spots | index time |
|---|---|---|---|---|
| pallets/flask `examples/tutorial` | 0 -> 12 | 0 / 21 -> 18 / 21 (3 URLs unknown) | 7 -> 0 | 0.05 s -> 0.06 s |
| fastapi/full-stack-fastapi-template `backend` | 0 -> 23 | 0 / 53 -> 53 / 53 | 23 -> 0 | 0.13 s -> 0.2 s |
| pallets/flask (whole repo, apps built inside its own tests) | 0 -> 156 | 0 / 331 -> 223 / 331 | 13 -> 0 | 0.6 s -> 0.9 s |

In the template, 17 of 23 routes carry an auth dependency (`CurrentUser`, `get_current_active_superuser`); the 6 without
are login, signup, password recovery, the health check and the private user route. The template's `deps.py` uses
Python 3.14's unparenthesized `except A, B:`; cg re-parses such files with the parentheses added, so it indexes on
older interpreters too. In the flask repository most unlinked requests target routes a test registers on the `app`
fixture it receives as a parameter (`def test_x(app, client): @app.route(...)`), which cg does not model.

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
