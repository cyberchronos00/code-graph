# Broadcast channels and tests

Two layers on top of the call graph: realtime channels (who may join a channel, what publishes on it, which client code
listens) and test code (which tests exercise a symbol, route or table). The examples below come from the task-board
fixture in `tests/broadcast_fixture` (a Laravel API in `api/` and a Nuxt client in `web/`, linked with `cg link`).

## Broadcast channels

### What is modelled

Backend (Laravel):

- Every `Broadcast::channel('name.{param}', callback)` in `routes/channels.php` (and in files loaded by
  `Broadcast::routes()` / `->withBroadcasting()` / `require`) becomes a `channel:<pattern>` node. The callback is the
  channel's authorization code: a closure, or a channel class (`join()`), with entry kind `channel_auth` and `CALLS`
  edges to everything it calls. Those calls are listed as the channel's **checks**.
- The broadcasting auth route (`Broadcast::routes()`, `withBroadcasting()` with its middleware, or an explicit
  `/broadcasting/auth` route) links to every channel with `AUTHORIZES_CHANNEL`.
- Events implementing `ShouldBroadcast` / `ShouldBroadcastNow`: `broadcastOn()` is evaluated to channel names (string
  literals, interpolation, concatenation, `sprintf`, `implode`, class constants, helper methods that build the name,
  constructor-promoted properties filled at the dispatch site). `BROADCASTS_ON` edges carry the evaluated name, the
  channel type (`Channel` = public, `PrivateChannel`, `PresenceChannel`) and the site the value came from. A channel
  whose publishers all use `PresenceChannel` is a presence channel.
- Dispatch sites (`event(new X)`, `X::dispatch()`, `broadcast(new X)`) are ordinary `INSTANTIATES` / `DISPATCHES`
  edges, so `impact` and `reaches` on an event class, or on its `broadcastOn`, go up to the routes, jobs and commands
  that fire it.

Client (TypeScript / JavaScript / Vue):

- Laravel Echo (`Echo.private(...)`, `.channel(...)`, `.join(...)`, `.listen('Event')`, `.listenForWhisper`,
  `.notification`), pusher-js (`pusher.subscribe('private-x')`, `.bind('event')`) and the `useEcho` family of hooks
  become `channel_sub:<name>` nodes with `SUBSCRIBES_CHANNEL` edges from the subscribing function. The channel name is
  evaluated like a URL template (`` `board.${id}` `` → `board.{id}`). Listeners chained on the subscription, on a
  variable that holds it, or on a property it was assigned to (`this.channel = Echo.join(...)`) are its events.
- `resources/js`, `resources/ts`, `resources/assets/js` and similar asset directories of a Laravel app are indexed as
  TypeScript sources, so Blade + Echo apps without a separate frontend repo are covered.

`cg link` (and, within one repo, `cg index`) matches subscriptions to backend channels: `MATCHES_CHANNEL` by pattern
shape (`board.{boardId}` ↔ `board.{board}`), and `LISTENS_FOR` from a subscription to the events it listens for (by
class name, `broadcastAs()` with a leading `.`, or the short class name).

### The `channels` query

```text
$ cg channels --db out/graph.db
channels: 6
  board.{board}  [private]  auth: App\Broadcasting\BoardChannel  checks: Broadcasting\BoardChannel::join, Support\BoardAccess::visibleBoardIds
      publishers: 1 (event:App\Events\TaskMoved)  subscribers: 1
  status  [public, undeclared]  auth: public: no auth
      publishers: 1 (event:App\Events\StatusPage)  subscribers: 1
  team.{teamId}  [private]  auth: closure routes/channels.php:11  checks: Models\User::belongsToTeam
      publishers: 2 (event:App\Events\TaskMoved, event:App\Events\TeamOnline)  subscribers: 0
      ! PUBLIC PUBLISH: 1 event(s) publish on a public Channel with this name (e.g. event:App\Events\TeamOnline); the authorization callback only guards private-/presence- subscriptions, so anyone can listen to these
  ...
client subscriptions without a backend channel: 1
  board.{boardId}.archive.events  [private]  events: BoardArchived
      subscribed in useBoardRealtime (useBoardRealtime.ts) @ frontend/app/composables/useBoardRealtime.ts:12
```

With a pattern, a concrete name (`board.42`) or a glob (`board.*`), one block per channel:

```text
$ cg channels board.42 --no-source --db out/graph.db
== channel board.{board}  [private]  @ backend/routes/channels.php:22
WHO CAN JOIN
  auth route route:POST /api/broadcasting/auth  middleware=['api', 'auth:sanctum']  (from ->withBroadcasting bootstrap/app.php:7)
  channel class App\Broadcasting\BoardChannel (join())
    HANDLED_BY Broadcasting\BoardChannel::join  @ backend/app/Broadcasting/BoardChannel.php:10 [exact]
    CALLS Support\BoardAccess::visibleBoardIds  @ backend/app/Broadcasting/BoardChannel.php:12 [exact]
PUBLISHED BY (1)
  event:App\Events\TaskMoved  name=board.{board_id}  [private]  broadcastOn @ app/Events/TaskMoved.php:22
      dispatched by Http\Controllers\TaskController::move @ backend/app/Http/Controllers/TaskController.php:22  http_route(1)  e.g. route:PATCH /tasks/{task}/move
LISTENED TO BY (1)
  board.{boardId}  [private]  events: TaskMoved  (exact)
      subscribed in useBoardRealtime (useBoardRealtime.ts) @ frontend/app/composables/useBoardRealtime.ts:5
      pages: page:app/pages/boards/[id].vue
      listens for backend events: event:App\Events\TaskMoved
```

Without `--no-source` the authorization callback's source is printed under WHO CAN JOIN. Flags:

- **UNDECLARED**: a private or presence channel with no `Broadcast::channel` callback; every client subscription to it
  is rejected by the auth route.
- **PUBLIC PUBLISH**: events publish on a public `Channel` whose name also has an authorization callback. The callback
  only guards `private-` / `presence-` subscriptions, so the public channel is open to anyone.
- **visibility mismatch**: the client subscribes with a different visibility than the backend publishes
  (`Echo.channel` for a `PrivateChannel`).
- Client subscriptions that match no backend channel are listed at the end.

MCP: `channels(pattern?, source?)`.

## Tests

### What is indexed

- PHP: PHPUnit test methods (`test_*`, `@test`, `#[Test]`) and Pest `it()` / `test()` cases (with `describe()`
  prefixes) under `tests/`.
- TypeScript / JavaScript: Vitest and Jest (`*.test.*`, `*.spec.*`, `__tests__/`), Playwright and Cypress spec files.
- Python: pytest and unittest. Test files follow pytest's rules: `test_*.py` / `*_test.py`, Django's `tests.py`,
  `conftest.py`, everything under `tests/` / `test/` and the configured `testpaths`, plus `pytest_plugins` modules.
  An application package named in `testpaths` (`testpaths = ["app"]`) contributes only the files pytest collects in
  it (and its `conftest.py` / `tests/` directories), so its modules stay application code.
  `python_files`, `python_classes`, `python_functions` and `testpaths` are read from `pytest.ini`, `pyproject.toml`
  (`[tool.pytest.ini_options]`), `tox.ini` or `setup.cfg` at the root or a nested project root. Test cases are pytest
  `test_*` functions and the methods of `Test*` classes (inherited ones too), and the `test*` methods of
  `unittest.TestCase` subclasses, Django `TestCase` / `TransactionTestCase` / `SimpleTestCase` and DRF `APITestCase`
  included. A class's `setUp` / `setUpClass` / `setUpTestData` / `tearDown` (and pytest's `setup_method` /
  `setup_module` style hooks) run with each of its cases. `@pytest.mark.parametrize` is kept on the test node
  (`attrs.params`: argument names, literal ids or values, case count), other marks in `attrs.marks`.
- Python fixtures: `@pytest.fixture` functions in the test module, its classes, every `conftest.py` up the directory
  tree and `pytest_plugins` modules. A test is linked to each fixture it requests (parameters, `usefixtures`,
  `request.getfixturevalue('x')`) and to the `autouse=True` fixtures in its scope; fixtures are linked to the fixtures
  they request, so what a fixture calls counts for every test that uses it. A fixture that overrides a fixture of the
  same name reaches the outer one, as in pytest.
- A test directory outside any package (pytest's default import mode) puts its modules on the import path, so
  `from helpers import build` in a test resolves to the helper next to it.

Each test case is a `test:` node with entry kind `test`. Edges made by test code are rewritten to non-propagating kinds,
so tests never count as callers and never widen `reaches`, `impact`, `writers`, `routes` or entry tagging. Apps,
routers and controllers that a TypeScript test builds for itself (`const app = express(); app.get(...)` in a
`*.spec.ts`, under `test/` or `__tests__/`) stay test code and never become `route:` nodes:

- `TEST_CALLS` / `TEST_USES`: test code calls, instantiates or touches application code (`attrs.orig` keeps the
  original edge kind).
- `TEST_HTTP`: a test sends a request to a route: `$this->getJson('/api/x')`, `->patchJson(route('tasks.move', …))`,
  Pest `get('/x')`, `$this->json('PATCH', '/x')`, Playwright `request.patch('/api/x')`, `cy.request`, Django
  `self.client.get(reverse('shop:book-detail', args=[…]))`, the pytest-django `client` / `admin_client` fixtures, DRF
  `APIClient` / `APITestCase.client`, FastAPI / Starlette `TestClient(app)`, `httpx.AsyncClient`, Flask
  `app.test_client()` (or a fixture that returns one). Python URLs are evaluated from literals, f-strings (with
  literal `parametrize` values filled in), `%` / `+` / `.format`, local variables, class attributes and `self.url = …`
  in `setUp`; `reverse()` / `reverse_lazy()` with a literal route name resolve through the Django URL names cg
  indexes (namespaces, DRF router names `<basename>-list` / `-detail` / `-<action>`), Flask `url_for('bp.view')`
  through blueprint endpoint names and Starlette / FastAPI `app.url_path_for('name')` through route names. Constant
  parts read from module constants and settings attributes (`f"{settings.API_V1_STR}/items/"`) are filled in. Project request
  helpers whose verb and URL are parameters (`$this->postAs('/x', …)` → `sendAs('post', $uri)` →
  `$this->json($method, $uri)`) are followed to their call sites. Requests are matched to routes like client calls in
  `cg link`; frontend tests are matched when the frontend is linked to its backend. A request whose URL is entirely
  unknown names no route, and a route name shared by many routes (unnamed routes inside a named group) is left
  unmatched.
- `TEST_VISITS`: a browser test opens a frontend page (`page.goto('/boards/1')`, `cy.visit`).

HTTP endpoints that only tests call are tagged `test_only` and kept out of the frontend → backend match rates.

### The `tests` query

```text
$ cg tests 'App\Support\BoardAccess::visibleBoardIds' --db out/graph.db
targets: 1 node(s): Support\BoardAccess::visibleBoardIds
tests: 2 direct, 1 transitive (of 10 test cases in the graph: phpunit 4, pest 3, playwright 2, vitest 1)

== DIRECT (the test code itself calls / requests the target): 2
  board access > it lists the boards of the user teams  [pest] backend/tests/Unit/BoardAccessTest.php:11  depth=1 conf=exact
      test:… -TEST_CALLS-> Support\BoardAccess::visibleBoardIds
  board access > admins see every board  [pest] backend/tests/Unit/BoardAccessTest.php:15  depth=1 conf=exact

== TRANSITIVE (through application code): 1
  board page > shows the tasks of a board  [playwright] frontend/e2e/board.spec.ts:4  depth=6 conf=exact
      test:e2e/board.spec.ts#… -TEST_VISITS-> page:app/pages/boards/[id].vue -USES_COMPOSABLE-> useBoardRealtime (useBoardRealtime.ts) -SUBSCRIBES_CHANNEL-> channel_sub:board.{boardId} -MATCHES_CHANNEL-> channel:board.{board} -HANDLED_BY-> Broadcasting\BoardChannel::join -CALLS-> Support\BoardAccess::visibleBoardIds
```

A Python example, from the bundled Django sample (`examples/bookstore-django`):

```text
$ cg tests catalog.api.get_book --db out/bookstore-django.db
targets: 1 node(s): catalog.api.get_book
tests: 0 direct, 1 transitive (of 10 test cases in the graph: pytest 5, unittest 5)

== TRANSITIVE (through application code): 1
  test_book_endpoints  [pytest] catalog/tests/test_api.py:13  depth=3 conf=exact
      test:catalog.tests.test_api.test_book_endpoints -TEST_CALLS-> catalog.tests.test_api.test_book_endpoints -TEST_HTTP-> route:GET /api/books/{book_id}/ -ROUTES_TO-> catalog.api.get_book
```

The header counts the test cases in the graph per framework; `cg coverage` prints the same for Python
(`python tests: 10 test cases (pytest 5, unittest 5) in 5 files, 5 fixtures; 9 HTTP test requests, 9 linked to
routes`). A function that only tests call has no callers in `impact` / `callers`, and the answer points to `tests`.

Targets: `Class::method`, `Class`, `route:VERB /uri`, `` `VERB /path` `` or `/path` (matched against route URIs),
`table.column`, or any node id. **Direct** means the test code itself calls or requests the target; **transitive**
means through application code (test → route → controller → service → target). Both lists start with the closest
tests (lowest depth). `--no-paths` drops the evidence chains, `--min-confidence` filters by the weakest edge.

MCP: `tests_covering(target, min_confidence?, paths?)`.

## Limits

- Channel names built at run time from values cg cannot evaluate keep a `{?}` segment; names that are entirely
  dynamic are skipped. Channels registered outside `Broadcast::channel` (custom broadcasters) are not modelled.
- Echo listeners registered through Livewire (`echo-private:…` listener keys in PHP components) and server-side
  pusher clients are not client subscriptions.
- A channel callback's checks are the methods it calls; whether a check really limits access is for the reader to
  judge (the query shows the callback source for that).
- Transitive test paths follow the code statically. A browser test that stubs the API (`page.route(...)`) still
  counts as reaching the backend through the page it visits.
- Test discovery follows file naming conventions; tests generated at run time (data providers expanding into cases,
  `test.each`, `@pytest.mark.parametrize`) are one node per declaration, with the parameters on the node.
- Python HTTP test requests link to routes of the web frameworks cg models (Django, DRF, django-ninja, FastAPI,
  Starlette, Flask); requests to any other framework are found and counted (`cg index` stats, `cg coverage`). A
  `url_for()` with an endpoint that `add_url_rule(endpoint=...)` names without a view function, and a URL built by a helper method from its arguments (`self._get_url('list')`) or
  passed as `**request` stays unknown.
- pytest hooks that generate tests or fixtures at run time (`pytest_generate_tests`, `pytest_collect_file`, fixtures
  registered by installed plugins other than `pytest_plugins` modules in the repo) are not followed; fixtures from
  installed plugins (`tmp_path`, `db`, pytest-django's `client`) are not graph nodes.
