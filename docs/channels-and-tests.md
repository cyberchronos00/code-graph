# Broadcast channels and tests

Realtime channels (who may join, what publishes, which client listens) and test code (which
tests exercise a symbol, route or table). Examples are the task-board fixture in
`tests/broadcast_fixture` (`api/` Laravel, `web/` Nuxt, joined with `cg link`).

## Broadcast channels

Laravel `Broadcast::channel('name.{param}', callback)` in `routes/channels.php` (and files
loaded by `Broadcast::routes()` / `withBroadcasting()` / `require`) becomes `channel:<pattern>`
. The callback is entry kind `channel_auth`. The auth route links to every channel with
`AUTHORIZES_CHANNEL`.

`ShouldBroadcast` / `ShouldBroadcastNow`: `broadcastOn()` is evaluated (literals,
interpolation, concatenation, `sprintf`, `implode`, constants, helpers, constructor properties
filled at the dispatch site). `BROADCASTS_ON` carries the name, the type (`Channel`,
`PrivateChannel`, `PresenceChannel`) and where the value came from. Publishers that all use
`PresenceChannel` make a presence channel. `event(new X)` / `X::dispatch()` / `broadcast(new X)`
stay ordinary `INSTANTIATES` / `DISPATCHES` edges.

Client (TS / JS / Vue): Laravel Echo (`.private` / `.channel` / `.join` / `.listen` /
`.listenForWhisper` / `.notification`), pusher-js (`subscribe` / `bind`) and `useEcho` become
`channel_sub:<name>` with `SUBSCRIBES_CHANNEL`. `` `board.${id}` `` becomes `board.{id}`.
Listeners chained on the subscription, or on a variable or property that holds it, are its
events. `resources/js` (and `resources/ts`, `resources/assets/js`) of a Laravel app are
indexed as TypeScript.

`cg link` (and `cg index` in one repo) adds `MATCHES_CHANNEL` by pattern shape (
`board.{boardId}` ↔ `board.{board}`) and `LISTENS_FOR` by class name, `broadcastAs()` (leading
`.`) or the short class name.

```text
$ cg channels board.42 --no-source --db out/graph.db
== channel board.{board}  [private]  @ backend/routes/channels.php:22
WHO CAN JOIN
  auth route route:POST /api/broadcasting/auth  middleware=['api', 'auth:sanctum']
  HANDLED_BY Broadcasting\BoardChannel::join  @ backend/app/Broadcasting/BoardChannel.php:10 [exact]
PUBLISHED BY (1)
  event:App\Events\TaskMoved  name=board.{board_id}  [private]
LISTENED TO BY (1)
  board.{boardId}  [private]  events: TaskMoved  (exact)
      subscribed in useBoardRealtime @ frontend/app/composables/useBoardRealtime.ts:5
```

| flag | meaning |
|---|---|
| UNDECLARED | private or presence channel with no `Broadcast::channel`; the auth route rejects every client |
| PUBLIC PUBLISH | events use a public `Channel` whose name also has an auth callback (the callback only guards `private-` / `presence-`) |
| visibility mismatch | `Echo.channel` for a `PrivateChannel` |
| (end of listing) | client subscriptions that match no backend channel |

A pattern, a concrete name (`board.42`) or a glob (`board.*`) prints one block. With no
pattern, one line per channel:

```text
$ cg channels --db out/graph.db
channels: 6
  board.{board}  [private]  auth: App\Broadcasting\BoardChannel  checks: BoardChannel::join, BoardAccess::visibleBoardIds
      publishers: 1 (event:App\Events\TaskMoved)  subscribers: 1
  status  [public, undeclared]  auth: public: no auth
  team.{teamId}  [private]  auth: closure routes/channels.php:11
      ! PUBLIC PUBLISH: an event publishes on a public Channel with this name
client subscriptions without a backend channel: 1
  board.{boardId}.archive.events  [private]  events: BoardArchived
```

Without `--no-source` the auth callback source is printed under WHO CAN JOIN. MCP:
`channels(pattern?, source?)`.

## Tests

Each test case is a `test:` node (`entry_kind` `test`). Edges from test code are rewritten so
tests never count as callers and never widen `reaches`, `impact`, `writers`, `routes` or
entry tagging. An app a TypeScript test builds for itself (`express()` inside `*.spec.ts`,
`test/` or `__tests__/`) never becomes a `route:`.

| language | what counts |
|---|---|
| PHP | PHPUnit `test_*`, `@test`, `#[Test]`; Pest `it()` / `test()` with `describe()` prefixes; under `tests/` |
| TS / JS | Vitest, Jest (`*.test.*`, `*.spec.*`, `__tests__/`), Playwright, Cypress |
| Python | pytest `test_*` and `Test*` methods (inherited too); unittest / Django / DRF `test*` methods. Files: `test_*.py`, `*_test.py`, Django `tests.py`, `conftest.py`, `tests/` / `test/`, `testpaths`, `pytest_plugins`. `python_files` / `python_classes` / `python_functions` / `testpaths` come from `pytest.ini`, `pyproject.toml`, `tox.ini`, `setup.cfg` |
| Swift | Swift Testing `@Test` (one node per declaration, `(parameterized)` when it has arguments); XCTest `test*` instance methods on an `XCTestCase` subclass. Files under `Tests/`, `*Tests/`, `*UITests/`, `*Tests.swift`, or any file that imports `XCTest` or `Testing` |
| Kotlin | `@Test`, `@ParameterizedTest`, `@RepeatedTest`, `@TestFactory`, `@TestTemplate` in `src/test`, `src/androidTest`, `*Test` source sets, `*Test.kt`. Framework from imports: `junit5`, `junit4`, `kotlin-test`, `testng`, `kotest` |

Swift Testing keeps the display name (`@Test("sums items")`), `.tags`, and the `.disabled` /
`.enabled` / `.bug` / `.timeLimit` / `.serialized` traits on the node. `@Suite` types, nested
suites included, carry `attrs.suite`; each test names its suite (`DiscountTests.Edge`). A
helper without `@Test` is not a case. XCTest requires `test*` instance methods with no
parameters, on a class whose superclass chain reaches `XCTestCase` (a project base class or an
external `*TestCase` counts).

`androidTest` counts as UI. A path through an `*Activity` is omitted unless `--through-roots` (
[Scope](#what-the-list-leaves-out)).

Python details that change classification:

- `testpaths = ["app"]` contributes only the files pytest collects there. A `tests.py` that
  application code imports and that defines no test case stays application code. A Django
  `tests.py` with `TestCase` classes stays test code.
- `@pytest.fixture` (module, class, `conftest.py` up the tree, `pytest_plugins`) links to the
  tests that request it, to `usefixtures` / `request.getfixturevalue`, and to `autouse=True`
  fixtures. An override reaches the outer fixture.
- A test directory outside a package is on the import path, so `from helpers import build`
  resolves next to the test.
- `setUp` / `setUpClass` / `setUpTestData` / `tearDown` and pytest `setup_method` /
  `setup_module` run with each case. `@pytest.mark.parametrize` is `attrs.params`; other marks
  are `attrs.marks`.

| edge | from test code |
|---|---|
| `TEST_CALLS` / `TEST_USES` | calls, instantiates or touches application code (`attrs.orig` keeps the original kind) |
| `TEST_HTTP` | `$this->getJson` / `patchJson(route(...))`, Pest `get`, Playwright `request.patch`, `cy.request`, Django `self.client.get(reverse(...))`, pytest-django `client`, DRF `APIClient`, FastAPI / Starlette `TestClient`, `httpx.AsyncClient`, Flask `test_client()` |
| `TEST_VISITS` | `page.goto`, `cy.visit` |

Python URLs come from literals, f-strings (literal `parametrize` values filled in), `%` / `+` /
`.format`, locals, class attributes and `self.url` in `setUp`. Constant parts of
`f"{settings.API_V1_STR}/items/"` are filled from module constants and settings attributes.
`reverse()` / `reverse_lazy()` use the Django names cg indexed (namespaces, DRF
`<basename>-list` / `-detail` / `-<action>`). Flask `url_for('bp.view')` uses blueprint
endpoint names. Starlette / FastAPI `url_path_for` uses route names. Helpers whose verb and URL
are parameters (`$this->postAs` → `sendAs` → `json`) are followed to the call site. A URL that
is entirely unknown, a `url_for()` whose `add_url_rule(endpoint=...)` names no view, or a route
name shared by a whole group, matches nothing. Frontend tests match when the frontend is linked
to its backend. HTTP endpoints that only tests call are tagged `test_only` and kept out of the
frontend → backend match rates.

Programs a test starts (`codegraph/process_runs.py`, Python also `plugins/python/subproc.py`):

| runtime | recognised start | links to |
|---|---|---|
| Python | `subprocess` / `asyncio.create_subprocess_*` / `os.system` with `[sys.executable, "-m", "pkg.cli"]`, a script path, or a console script | `script:pkg.cli`, the file's entry, or `script:console_scripts:<name>` |
| Rust | `CARGO_BIN_EXE_x`, `Command::cargo_bin("x")` | `main` of bin `x` |
| Node | `spawn` / `execa` / `fork` of a project script or `package.json` `bin` | the script's module |
| PHP | `Process(['php', 'artisan', 'x'])`, `$this->artisan('x')` | the command (`DISPATCHES` when in-process) |
| Dart | `Process.run` / `TestProcess.start` of a project `.dart` file | that file's `main` |

A helper whose program is a parameter is followed through up to 5 helpers (`run_django_admin` →
`run_test` → `popen`), including `append` / `extend` / `insert` / `+=` between the assignment
and the call. A `for` over a known list contributes the loop items; any other loop adds one
unknown item. `CliRunner().invoke` on a click or typer app, `scripttest.TestFileEnvironment.run`, pytest `Pytester.run` / `pytester` / `testdir`, `sh.mytool` and plumbum `local["mytool"]`
stand in for a direct `subprocess`. A `-c` snippet links to what it calls (`how: -c`), or to
a project module it only imports (`how: -c import`). A script the same function copies first (
`shutil.copy`, `dst.write_text(src.read_text())`) links to that source (`how: copied script`). A `manage.py-tpl` template is read as Python (`how: copied template`). `git`, `-m pip` and
unknown argument lists add nothing; the Python plugin counts them under `subprocess`.

Not linked: Go (`exec.Command`, `go run ./cmd/x`, no Go plugin); arguments built in another
function or read from configuration. A script outside every source dir that a test runs (
`node tools/gen.js` beside `src/`) is still a source file, so it has a module node.

```text
$ cg tests 'App\Support\BoardAccess::visibleBoardIds' --db out/graph.db
tests: 2 direct, 0 nearby transitive (app depth <= 3), 1 UI / snapshot (of 10: phpunit 4, pest 3, playwright 2, vitest 1)
== DIRECT: 2
  board access > it lists the boards of the user teams  [pest]  depth=1 conf=exact
      test:… -TEST_CALLS-> Support\BoardAccess::visibleBoardIds
== UI / SNAPSHOT (through application code): 1
  board page > shows the tasks of a board  [playwright]  depth=6 conf=exact  app_depth=3
      test -TEST_VISITS-> page:… -USES_COMPOSABLE-> useBoardRealtime -SUBSCRIBES_CHANNEL-> … -CALLS-> visibleBoardIds

$ cg tests catalog.api.get_book --db out/bookstore-django.db
tests: 0 direct, 1 nearby transitive (of 10: pytest 5, unittest 5)
== TRANSITIVE: 1
  test_book_endpoints  [pytest] catalog/tests/test_api.py:13  depth=3 conf=exact  app_depth=0
      test -TEST_HTTP-> route:GET /api/books/{book_id}/ -ROUTES_TO-> catalog.api.get_book
```

Targets: `Class::method`, `Class`, `route:VERB /uri`, `` `VERB /path` ``, `/path`,
`table.column`, or any node id. **Direct** is the test itself. **Transitive** is through
application code. Both lists start with the closest tests. `--no-paths` drops the chains.
`--min-confidence` filters by the weakest edge. A `(candidate)` line is a Swift or Kotlin call
whose receiver type is unknown, linked to each of two to five same-name methods (
`attrs.binding` `candidate`). `impact` and MCP `callers` mark those callers the same way.

`cg coverage` prints the same framework counts (
`python tests: 10 test cases (pytest 5, unittest 5) …`). A function that only tests call has no
callers in `impact`; the answer points at `tests`.

### What the list leaves out

| rule | default |
|---|---|
| App depth | at most `--max-depth` hops (default 3) through application code. Test edges and wiring (route → handler, `MATCHES_*`, `HANDLED_BY`, middleware) do not count. `--max-depth 0` lists every depth |
| UI / snapshot | Playwright, Cypress, `*UITests`, `androidTest`, `uiTest`, `integration_test`, snapshot / screenshot names. `--unit-only` drops them |
| App roots | a path through `@main` / `UIApplicationDelegate`, a Kotlin `*Activity`, or `--exclude-root SPEC` is omitted. `--through-roots` keeps them |

`test → route → controller → service → target` has app depth 2. A line shows `app_depth=N` when
it differs from `depth`. The header and a `not listed:` line count what was left out (
`1 more not listed (1 deeper, 1 through roots, 1 ui)`), with the options that bring it back.
`--json` carries `ui`, `omitted` (`deeper`, `through_roots`, `ui`) and `limits`.

When no test reaches the target, the last line says why: the graph has no test code, it has test
files but no recognised cases, or it has N cases and none call the target. On a base or
interface method the tests of its overrides count, marked `(via override A.m)` (`via_override`
in `--json`). `Sub.method` for an inherited method resolves to the definition it inherits. A
root that is itself the target is not excluded. An explicit `--exclude-root` still applies with
`--through-roots`.

MCP:
`tests_covering(target, min_confidence?, paths?, max_depth? = 3, unit_only?, exclude_roots?, through_roots?)`
. `max_depth` 0 means any depth.

## Limits

- Channel names cg cannot evaluate keep `{?}`; a fully dynamic name is skipped. Custom
  broadcasters and Livewire `echo-private:` keys are not client subscriptions.
- A channel callback's checks are the methods it calls. The query prints the source; it does not
  decide whether the check limits access.
- A browser test that stubs the API (`page.route`) still counts as reaching the backend
  through the page it visits.
- `test.each`, `@pytest.mark.parametrize`, Swift `@Test(arguments:)` and `@ParameterizedTest`
  are one node per declaration.
- Python HTTP tests link to Django, DRF, django-ninja, FastAPI, Starlette and Flask. Other
  frameworks are counted in the index stats. `pytest_generate_tests` and fixtures from installed
  plugins are not graph nodes.
