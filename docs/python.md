# Python

What `.py` adds beyond [Install](install.md), [CLI specs](cli.md#query-targets-specs), and [Graph schema](schema.md): source roots and module names, reference `how` values, and FastAPI / Starlette / Flask route ids.
The parser is the stdlib `ast` module. A checkout needs no virtualenv, and the project is never imported.
Why a file is unmapped: `cg coverage`. The `python.source_roots` key: [Configuration](configuration.md).

## Source roots

A source root is a directory imported by top-level name.
The plugin runs on any repo with `.py` files outside dependency, build, and cache directories.
Detection records a reason per root. Roots in `.cg.yaml` replace detection (`.` is the indexed root).
`cg index --python-root` (repeatable) overrides the file for one run. Paths stay inside the indexed root.
`sys.path` edits at run time are not evaluated.

| evidence | example | reason |
|---|---|---|
| indexed root | `app/views.py` → `app.views` | `indexed root` |
| packaging config | setuptools, hatch, poetry, pdm, maturin `package-dir` / `where` / `sources`; `setup.cfg`; a literal `package_dir=` or `find_packages` in `setup.py` | the key that matched |
| `src/`, `lib/`, `python/` | conventional directories that are not packages | `lib/ directory` |
| nested project, depth ≤ 4 | `pyproject.toml`, `setup.py`, `setup.cfg`, or `manage.py` | `nested project (backend/manage.py)` |
| PEP 420 | `plugins/acme/core/base.py` imported as `acme.core.base` | `namespace package acme` |
| package parent | `services/billing/billing/__init__.py` | `parent of top-level package billing` |

A `package-dir` map (`{"core" = "lib"}`) makes `lib/` hold package `core`.

## Module names

A file under two roots gets one canonical name: the root whose top-level name the project's imports use most, and the deepest root on a tie.
The other names stay aliases, so both spellings resolve to the same node.
`cg coverage` reports modules whose imports used more than one name.

When two trees claim one name (often `tests` in a monorepo), the tree with the most import evidence keeps it, then a tree with no other importable name, then the shallower root.
The others are named from the indexed root (`svc-b.tests.conftest`). Relative imports inside each tree still resolve.

A path that is not an importable name (`my-scripts/run.py`, `alembic/versions/1a2b_add.py`) is `unmapped` ([Completeness](completeness.md)).
With configured roots, files outside them are `unmapped` too, and the coverage hint names `python.source_roots`.

```console
$ cg coverage --db out/proj.db
coverage proj: python 3 exact
  python source roots: lib/ (detected: lib/ directory; parent of top-level package core, 3 modules)
```

A layout that only uses the indexed root keeps that short output (`--all-files` shows the root).
Index stats: `roots_mode` (`detected`, `configured`, `flag`), `source_roots` (`path`, `origin`, `why`, `modules`, `package` for a mapped directory), `roots_warnings`, `roots_ambiguous`, `module_name_collisions`.
MCP `coverage(json_output=true)` adds `python_source_roots`.

## Resolution

| layer | what | label |
|---|---|---|
| parser | stdlib `ast` of the running interpreter. Coverage calls this parser `exact` | parsed, or listed by `cg coverage --details` |
| imports | relative imports, `__init__` re-exports, aliases, function-local imports | `exact` / `resolved` |
| calls | `self` / `cls` with MRO, annotations, constructor results, `super()`, a few container generics | `resolved` |
| fallback | the one method of that name in the project | `heuristic` |
| wrappers | `sync_to_async(f)(...)` and the same shape | a call to `f` |

These stay unresolved or heuristic: untyped parameters, `**kwargs`, decorators that change the signature, `getattr(obj, name)`, and a table filled in a loop or in another function.
A decorator registration or `registry[key] = fn` whose caller is outside the graph is a blind spot (`python_decorator_registration`, `python_registry_assignment`).

## Query specs

`Class.method` and `Class::method` follow [query targets](cli.md#query-targets-specs). Python-only shapes:

| spec or id | selects |
|---|---|
| `module:pkg.mod`, `function:pkg.fn`, `method:pkg.Class.method`, `class:pkg.Class` | the declaration |
| `script:<module>`, `script:console_scripts:<name>`, `script:<group>:<name>` | a program entry ([below](#entry-points-and-function-references)) |
| `field:<Class>.<attr>` | a stored attribute ([below](#stored-attributes)) |
| `route:GET /api/v1/items/{id}` | FastAPI, Starlette, or Flask. A host is part of the id: `route:GET / @api.example.org`, `route:GET / @api.*`. The displayed name stays `GET /` |
| `test:<module>.<Class>.<method>` | a pytest or unittest case (`attrs.params` on a parameterised case) |

## Stored attributes

`field:<Class>.<attr>` is a stored attribute (`property: stored`):

- instance attributes assigned as `self.x` in any method (`declared: self`);
- annotated class attributes, such as dataclass, pydantic, or attrs fields (`declared: class`). `ClassVar`, `Final`, and `InitVar` are excluded.

Plain class assignments stay constants or descriptors. Methods, properties, and nested classes are never fields.
Django model fields stay the Django plugin's own `field:` nodes.

`cg readers Class.attr` / `cg writers Class.attr` list `READS_PROP` / `WRITES_PROP` (`resolved`, attr `receiver`) when the receiver's class is known: `self`, an annotated parameter, `c = Cart()`, or `self.app.name`.
An unknown receiver adds no edge. Writes are assignment, augmented assignment, `del`, in-place list / dict / set / deque methods (`via: mutating`), and item assignment (`self.cache[k] = v`, `via: item`).
Test-code accesses are `TEST_USES`.

## Entry points and function references

A function used as a value gets `REFERENCES_FN` from the function, method, class, or module that mentions it.

| `how` | example |
|---|---|
| `collection` | `CHECKS = (check_size, check_owner)`, `HANDLERS = {"create": on_create}`, `.append` / `.add` / `.insert` / `.extend` / `.setdefault` |
| `callback` | `executor.submit(job)`, `sorted(items, key=by_name)`, `Thread(target=worker)`, `signal.connect(receiver)` |
| `assignment` | `handler = on_create`, `self.hook = fallback` |
| `decorator` | a local `@retry` references the function it wraps |

A call through that table (`HANDLERS[kind](e)`, `for check in CHECKS`) is `CALLS` with `via="collection"` at `resolved`, one edge per function the table can hold.
Elements are followed through copies, `list()` / `sorted()`, filtering comprehensions, and a helper that returns the collection or a constant key of it. Those steps chain.
A loop over a list of instances calls the method each item's class has. Items of unknown type add no edge. A local decorator also gets `CALLS` with `via="decorator"`.
`impact` prints `(ref: collection | callback | assignment | decorator)` and `(call through a collection)`.

| source | id | entry |
|---|---|---|
| `if __name__ == "__main__"` | `script:<module>` | `main` — `python -m pkg.mod` or `python file.py` |
| `pkg/__main__.py` | `script:pkg.__main__` | `main` — `python -m pkg` |
| `[project.scripts]` / `[project.gui-scripts]`, Poetry, Flit, setup.cfg `console_scripts`, a literal `entry_points` in `setup.py` | `script:console_scripts:<name>` | `main` |
| other groups (`[project.entry-points.<group>]`, Poetry plugins, setup.cfg / setup.py groups) | `script:<group>:<name>` | `public_api` |
| FastMCP `@mcp.tool()`, `mcp.tool()(fn)`, `add_tool`, resources, prompts | the function | `message_handler` |
| click, asyncclick, rich-click, cloup, typer, Flask `@app.cli.command()` | the function | `cli_command` |

`pkg.mod:func` is `CALLS` with `via="entry_point"`.
A class target references that class's methods. A module target references its public functions. A typer or click object runs its registered commands.
A local wrapper that registers the function it decorates passes the entry on to every function it wraps.
Targets that cannot be resolved increment `entry_points_unresolved` (samples in the stats).
An external decorator with a receiver (`@app.route`, `@bus.subscribe`) is a `heuristic` reference (`registry="external"`).
Views Django already wires through `urls.py` keep the route edge.
Code that runs because a module was imported is not its own entry. Entry points computed by executing `setup.py` are not read.

```console
$ cg impact checks.check_size --db out/checks.db
  d=1 [function] checks.validate  (call through a collection)
  d=3 [script] console script checks
```

Plugin stats: `references`, `decorator_calls`, `script_entries`, `registrations`, `entry_points_unresolved`, `references_superseded`.

## Tests (pytest and unittest)

Files pytest would collect are test code: `python_files`, `python_classes`, `python_functions`, and `testpaths` from `pytest.ini`, `pyproject.toml`, `tox.ini`, or `setup.cfg`, plus unittest cases.
`cg tests` lists direct and transitive tests. `impact`, `callers`, and `reaches` count application callers only.

Fixture chains, Django / DRF / FastAPI / Flask HTTP test requests, and a subprocess that starts a project program (`TEST_CALLS`, `via: subprocess`, helpers followed up to 5 calls) are in [tests](channels-and-tests.md#tests).
`cg coverage` adds a `python tests:` line: cases per framework, test files, fixtures, HTTP test requests, and how many reached a route.
The plugin stats carry the same under `tests` and `subprocess` (`linked`, `outside_project`, `unresolved`, with samples).

## Web routes

A project that depends on or imports `fastapi` / `starlette` or `flask` gets `route:` nodes and `ROUTES_TO` the handler.
The app, router, or blueprint is whatever the code assigns, returns, or receives (`create_app()`, a parameter, a pytest fixture of that name).
A Flask subclass defined inside a function is a factory. Paths come from literals, f-strings, module constants, and settings attributes (`settings.API_V1_STR`). An unevaluable piece is `{?}`.

| framework | what becomes a route |
|---|---|
| FastAPI | `@router.get` / `post` / …, `api_route`, `add_api_route`, `@app.websocket`, `include_router` prefixes, `dependencies=` |
| Starlette | `Route`, `Mount`, `WebSocketRoute`, `Router`, `add_route`, `HTTPEndpoint` (one route per method). `{id:int}` → `{id}`. `Host("api.example.com", …)` sets `host`, not the path |
| Flask | `@bp.route`, `@bp.get`, `add_url_rule`, `MethodView.as_view`, nested `register_blueprint` (the registration prefix replaces the blueprint's). `<int:id>` → `{id}`, `<path:p>` → `{p*}`. The endpoint name is what `url_for` takes. Built-in `GET /static/{filename}` (`static: true`; none when `static_folder=None`) |
| class views | fastapi-utils / fastapi-restful `@cbv`, classy-fastapi `Routable`, flask-restful / flask-restx `add_resource` / `@ns.route`, Flask `View` / `MethodView` (`methods`, `dispatch_request`), Flask-Classful `X.register` |
| Django | `http_route` (urls, django-ninja, DRF), `websocket` (Channels), `queue_job` (Celery), `listener` (signals), `management_command`, `admin_panel`. GraphQL root fields are protocol endpoints ([Protocol links](protocols.md)) |

A router or blueprint no app includes keeps its routes with `mounted: false` and no entry point.
A `for` over a literal list of `(path, view)` is unrolled, one registration per element.
`host` and `subdomain` are route attributes and part of the node id, so the same method and path on two hosts are two nodes.
`add_url_rule(..., endpoint="index")` without a view takes `@app.endpoint("index")`, `view_functions["index"]`, or another rule with that endpoint (`endpoint_alias`).
A blueprint registered twice (`name=`, another `url_prefix`) gets both sets of routes.

Access for `cg routes --unguarded`:

| source | recorded as |
|---|---|
| `Depends` / `Security`, including `Annotated` aliases and `dependencies=` on the route, router, or `include_router` | `attrs.access`. `Depends(RoleChecker("admin"))` is read from `__call__` |
| statuses, security schemes (`OAuth2PasswordBearer`, `HTTPBearer`, `APIKeyHeader`), headers, nested dependencies to depth 4 | `checks`. `auto_error=False` does not reject. `effect` is `rejects` (401 / 403), `raises`, or `reads` |
| a rejecting dependency, whatever its name | an auth guard (`auth_by: dependency check`). A status raised in a helper it calls counts (`checks.checked_in`, `in <helper>`) |
| Flask decorators such as `@login_required` | access on that view |
| `app.add_middleware(X)` when `X` is a project class, or a library class named like access control | `via: middleware` on that app's routes. CORS and GZip are skipped |

Checks written in the handler body are not guards.

`TestClient(app)`, `app.test_client()`, and fixtures that return one link to the route (`TEST_HTTP`), so `cg tests <handler>` lists them.
When several routes fit, cg narrows by the request host (`subdomain=`, `base_url=`, a `Host` header, an absolute URL), then by routes the same test function registered, then the same module.
A request that still has more than three candidates is dropped. An all-parameter route needs one literal segment unless that test registered it.
`app.dependency_overrides[dep] = fake` (in the test, a fixture it uses, or at module level) sets `dependency_overrides` on the edge.

Sanic, Litestar, and Bottle decorators stay the `python_decorator_routes` blind spot.
A pluggy-style registry that returns views, and a route added by an unknown library call, stay unmodelled.
Django `include()` targets and `urlpatterns` built in a loop are blind spots `django_unresolved_include` and `django_dynamic_urlpatterns` ([blind spots](completeness.md#blind-spots)).

## Validation

Public-corpus counts for source roots, references, tests, and these routes are in [How we validate](validation.md).
