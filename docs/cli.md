# CLI reference

All commands: `python -m codegraph.cli <command> …` (the README defines a `cg` shell function for this). Every query command needs `--db`.

## Commands at a glance

- `index ROOT --db DB [--name N] [--gates FILE] [--scip FILE] [--python-root DIR]... [--no-apps] [--include-generated]`: detect languages/frameworks and
  build the graph. Prints the stats JSON on stdout (including the detected frameworks and applied presets in
  `presets`, and the starter queries in `starters`, with `starters_skipped` naming any that did not finish within
  their 20 s budget) and a per-language coverage summary on stderr; a missing toolchain
  skips that language with a note instead of failing the index. Reads `.cg.yaml` at ROOT when present
  ([configuration.md](configuration.md#project-config-file-cgyaml)); `--python-root` sets the Python source roots for
  this run ([python.md](python.md)). Generated, copied and vendored files (build output, generated clients, Capacitor /
  Cordova web copies) stay out of the graph and are listed by `coverage`; `--include-generated` indexes them, labelled
  `attrs.generated` ([generated.md](generated.md)). An invalid `.cg.yaml` exits with status 2 and a message naming the key.
  When `.cg.yaml` lists monorepo `apps`, one run indexes each app and links each frontend / backend pair
  ([configuration.md](configuration.md#monorepo-apps)); `--no-apps` indexes ROOT as one project.
- `doctor [ROOT] [--json]`: what this installation can index: cg, Python and tool versions, whether the TypeScript /
  PHP / Dart extractor dependencies are installed (and where), and per language exact / heuristic / unavailable with
  the reason and the command that installs what is missing; with ROOT only the project's languages and its own
  conditions (`compile_commands.json`, `.cg.yaml` `rust.targets`) and `project:` checks: the root `tsconfig.json` or
  the per-package tsconfigs indexed as one program, the Gradle / Maven build file and Kotlin version (Android modules
  named), and the root `Package.swift` or the Xcode projects that need `CODEGRAPH_SWIFT_INDEX_STORE`. It imports every cg module and exits with status 1,
  naming the module and the error, when one does not import on the running Python. [install.md](install.md)
- `setup [typescript] [php] [dart] [--quiet]`: install the extractor dependencies now (into the user cache for an
  installed cg) instead of on the first index; default: every language whose toolchain is installed.
  `setup --prune [--dry-run]` removes the extractor installs this cg does not use (left behind by an update that
  changed a lock file) and prints them with their sizes; an install in progress (its lock held) is kept.
- `clean [ROOT] [--all [--extractors]] [--stale] [--db DB] [--dry-run] [--json]`: remove cache entries: those of one
  project (and of every project indexed below ROOT), the stale ones, or all of them except the extractors; `--db`
  also deletes a graph DB with its `-wal` / `-shm` files. `doctor` shows the cache size per kind ([clean](#clean)).
- `coverage --db DB [--json] [--details] [--all-files]`: a short summary by default (one line per repo, one per
  language that is not fully indexed with its reason, Python source roots when the layout uses more than the indexed
  root, syntax error counts, files per platform target, blind spots;
  `cg index` prints the same on stderr), the full report with `--details` (or `--all-files`): which languages and files the index covers: parser mode (`exact`,
  `heuristic` when the exact-mode indexer is missing, `skipped` when the toolchain is missing, with the install hint),
  file completeness (discovered / indexed / parse failed / over size limit / unmapped / excluded, the first 5 paths
  per bucket or all with `--all-files`), unsupported source types (by extension or `#!` line) and blind spots (route /
  handler registrations cg does not model, with `file:line`). For Python, the source roots with their origin
  (detected with the reason, or configured) when the layout uses more than the indexed root. On a combined graph, one block per linked repo (stored
  by `link`, so it also works after the source DBs are gone). See [completeness.md](completeness.md).
- `config show|validate [ROOT]`: the effective configuration of a project, one row per value with where it comes
  from (flag, `.cg.yaml`, framework preset, detection); `validate` checks `.cg.yaml` and exits with status 2 when it
  is invalid. Flags as for `index`, `routes` and `serve` show their effect. See
  [configuration.md](configuration.md#checking-a-config-file).
- `starters --db DB [--json]`: starter queries derived from the graph, each with the matching command and MCP call
  (a write route without an auth guard, the most-written and most-read tables, the busiest connection and env key, the
  page with the largest backend reach, the most-called functions). The visual view's landing page offers them too.
- `link --backend DB --frontend DB --db OUT`: merge a backend and a frontend graph and match client HTTP calls to routes.
- `reaches SPEC... [--gate auto/none/NAME]`: everything that depends on the targets, grouped by entry classification.
  On a base or interface method it also follows the overrides (listed as `overrides followed`), and the dependents
  reached only through one are marked `(via override A.m)`.
- `impact METHOD [--plans-dir DIR]`: reverse walk from a method up to its entry points. A caller that holds the
  function as a value instead of calling it is marked `(ref: collection | callback | assignment | decorator)`, and a
  call through a dispatch table or plugin list `(call through a collection)`. The override relation has its own
  lines: `overrides: Base.m` and `overridden by: A.m, B.m`. A base method is not listed as a caller of its override;
  code that calls the base declaration is, marked `(via base Base.m)`. `impact Base.m` on an abstract or base method
  also lists the code that calls its overrides (calls through a plugin list or a base-typed value land there),
  marked `(via override A.m +1)`, so the base API shows the same callers as its implementations. With `--plans-dir`, external
  clients recorded in snapshot files there (`snapshot_version` + `calls`) that call an affected route are listed too.
- `downstream SPEC`: forward dependencies (page → composables → HTTP → routes → services → tables).
- `path SRC DST`: one shortest evidence chain. A `table:` target with no direct table edge on the way falls back to
  its columns (the path then ends at the column that is read or written). When a hop passes keys that the next request
  never sends, a `note: sent but not forwarded: …` line follows the path. Without a path it prints `no path …` with
  the reason (for example a path that exists in the other direction) and exits with status 1.
- `routes [--writes [TABLE]] [--reaches SPEC...] [--missing NAME] [--unguarded] [--auth-pattern RE]`: routes with their
  middleware / guards / auth, scoped to what they write or reach, with one evidence chain each and the frontend
  callers on a combined graph (see [Routes and guards](#routes-and-guards)).
- `search NAME [--kind K]`: nodes by name / FQN substring (with their root-relative `file:line`), plus the routes whose
  middleware, guard or auth names match.
- `writers TABLE`, `writers Type.prop` / `readers Type.prop`, `siblings SYMBOL`, `node SPEC`, `stats`: writers of a
  table, writers / readers of a stored property (Swift, Kotlin, Python, TypeScript, PHP; #88), similar code, node
  details, counts.
- `roundtrip Type.prop [--json] [--tests]`: heuristic. It reports each write of the property that passes through a
  lossy transform and each read that seeds UI state, and pairs them with any wider range drawn next to the read
  (see [`roundtrip`](#roundtrip)).
- `lint async-state [--json] [--tests]`: heuristic, rules `stale-async-result` (an awaited result written to state
  with no cancellation or token check) and `two-writers` (state written by lifecycle code and by an async
  callback). See [`lint`](#lint).
  `node` prints the node (location, fqn, platforms, attrs) and its outgoing / incoming edges, the same edge from one
  site once with a count; `stats` prints the project, its languages, the coverage summary line and the node / edge
  counts. With `--json` each prints one JSON document (`node`: a list of `{node, out, in}`; `stats`: `{project, root,
  indexed_at, nodes, edges, nodes_by_kind, edges_by_kind, stats}` with the full index stats).
  `siblings` prints text (`--json` for the raw result).
- `api-calls SPEC`: client endpoints with call sites, request keys and the matched route. SPEC is `all`, `unmatched`, a
  substring, or a `*` glob matched against the endpoint, its path, the route, the controller, the caller or the
  call-site file (`'GET /v1/*/orders*'`, `'*/staff/*'`, `'*useOrders*'`). Endpoints whose base URL comes from runtime
  config or env show the folded value (`(base {runtimeConfig.apiBase} = http://localhost:8000/api, nuxt.config.ts:6)`);
  endpoints only tests call are marked `(called from tests only)`.
- `channels [PATTERN] [--no-source]`: broadcast channels: who can join (auth route, callback, checks), which events
  publish on it, which client code listens. PATTERN is a channel pattern, a concrete name or a glob. See
  [channels-and-tests.md](channels-and-tests.md#broadcast-channels).
- `bridges [PATTERN] [--protocol P] [--unmatched]`: web / native bridge endpoints (Capacitor plugins, React Native /
  Expo modules, Flutter channels) and desktop process boundaries (Electron IPC channels and context-bridge members,
  Tauri commands): senders, receivers per platform or process, methods missing on a platform, without a receiver,
  unregistered or implemented outside the repo. See [bridges.md](bridges.md).
- `protocols [PATTERN] [--protocol P] [--side send|receive] [--unmatched]`: every protocol endpoint (HTTP calls and
  routes, Pusher channels, Nest messages, jobs, events, bridges, Socket.IO ...): a summary per protocol, or senders,
  receivers, guards, matches and checks (no_receiver, no_sender, ambiguous, schema_mismatch, unguarded) per endpoint.
  See [protocols.md](protocols.md).
- `tools [PATTERN] [--framework F] [--agent A] [--unmatched]`: LLM tools and MCP tools / resources / prompts: handler,
  tables it reaches, agents offering them, callers and checks (no_receiver, no_sender, name_collision); agents,
  dynamic dispatch and model calls. See [ai-tools.md](ai-tools.md).
- `external [PATTERN] [--protocol P] [--source S] [--tls-off]`: external systems (databases, caches, brokers, mail
  relays, directories, file-transfer hosts, object stores, third-party HTTP hosts) with the code and connections
  using them, address source, credential source (location only) and TLS. See [external.md](external.md).
- `parity --db SRC --against TGT [--map FILE] [--strip-prefix WORD]... [--no-fuzzy] [--structure [--no-learn]
  [--write-map FILE]]`: port gap report, the types, functions, enum cases and constants of SRC with no counterpart in
  TGT (an iOS app and its Android port), grouped by folder, with the match confidence; `--structure` also pairs
  renamed symbols by what they use, with the evidence. See [parity.md](parity.md).
- `tests SPEC [--no-paths]`: the tests that exercise a symbol, route or table, direct and transitive. See
  [channels-and-tests.md](channels-and-tests.md#tests).
- `platforms [summary|divergence] [--target T] [--kind K]`: platform-specific code: the project's targets and where
  they come from, conditions and tagged symbols per target; `divergence` lists variants that leave a target
  uncovered, API differences between variants, and references to code that is not built on a target. See
  [platforms.md](platforms.md).
- `resolutions CONCEPT [--within S]`: where a value is resolved, its fallback chains, and whether the client sends it.
- `plan {list,load,validate,check,baseline} NAME [--verify] [--summary]`: the planned-change layer. `--summary` prints
  counts per section and check plus the top `--max-items` items (default 5).
- `serve`, `viz-export`, `viz-plan`: the visual view (local server or self-contained HTML).

Most query commands take `--json`, `--min-confidence resolved` (or `exact`) and `--max-depth`. `reaches`, `impact`,
`downstream`, `path`, `routes` and `search` take `--platform TARGET` (windows, linux, macos, ios, android, web) for one
target's build: code under a platform condition that is false there is left out, and the answer's first line names
the filter and how many conditions could not be evaluated ([platforms.md](platforms.md#filtering-queries---platform)).

## Query targets (specs)

- `table.column` or `column:table.column`: a DB column. `table:orders`: a DB table.
- `connection:warehouse`, `connection:tenant_*`: DB connection(s) from `config/database.php`, plus dynamic ones
  registered via `Config::set('database.connections.…')`.
- `env:WAREHOUSE_DB_HOST`, `config:database.connections.warehouse`: env / config keys.
- `Class.method` or `Class::method`, `Class`, short or FQN: code symbols (suffix match). Either separator works in every
  language: a spec that matches nothing with its own separator is retried with the other one (Swift / Kotlin / Python /
  TS / Dart fqns use `.`, PHP / Rust / C++ `::`).
- `Sub.method` / `Sub::method` for a method `Sub` inherits without redefining it: resolves through the class's
  ancestors (EXTENDS / IMPLEMENTS / trait use, nearest first) to the definition, and the answer says so
  (`B.run -> inherited from Base.run`, short names; `--json` has the fqns). The callers are those of the inherited
  definition, narrowed by receiver type: a call whose receiver is known (the TypeScript checker type, a Python
  inferred instance, the elements of a collection it loops over, a Kotlin / Swift / Dart / PHP typed value or the
  implicit `this` of a subclass; edge `attrs.recv`) to be a class that cannot be a
  `Sub` (a sibling, neither `Sub`, a subclass nor an ancestor) is left out, and the note counts them
  (`callers narrowed to B: 2 of 6 calls on other classes left out`). Calls with an unknown receiver, and receivers
  whose ancestry the graph does not know (a mixin `extends mix(Base)`), are kept. `reaches` and `tests` narrow the
  same way. Of its overrides only those in `Sub` and its subclasses are followed.
- `page:/reports/:id`: a Nuxt page by its route path. `app/pages/x.vue`, `app/composables/useX.ts`: a TS module or Vue
  SFC by file (repo-relative, suffix match). `useX`, `useX.fn`, `fn`: a TS composable, store or function.
- `src/app.ts#listOrders`, `app.ts#listOrders`, `src/svc.ts#OrderService.create`: a TypeScript / JavaScript / Vue
  symbol in one file, for a name declared in several files. The file part matches the node's file exactly or as a
  path suffix; the name part is the declared name (`Class.method` for a member; a bare member name such as
  `svc.ts#create` works when the file declares nothing else under that name). A name declared twice in one file
  selects both (`codec.ts#decode`; `codec.ts#decode~2` is the second one). The full id
  (`function:src/app.ts#listOrders`) works too.
- `http:GET /v1/{store}/…` and `route:GET /v1/{store}/…`: a client endpoint / a backend route (`*` glob).
- `channel:orders.{order}`, `channel_sub:orders.{id}`: a backend broadcast channel / a client subscription.
- `test:tests/Feature/OrderTest.php::…`: a test case (see `cg tests`).
- `request_key:timezone`, `setting:reports.timezone`: value facts (see [value-facts.md](value-facts.md)).

- Rust / C / C++ (see [native.md](native.md#query-specs)): `crate::module::Type::method`, `Type::method` (also
  matches `<Type as Trait>::method`), `ns::Class::method`, `rb_create`, `mod:kv_core::store` or `kv_core::store` (every
  function in a module), a source file path, and the fact nodes `feature:<pkg>/<name>`, `cfg:unix`, `define:MACRO`,
  `unsafe:<crate>`, `env:KEY`.

Several specs in one `reaches` call are unioned.

## Empty results

When `impact`, `writers`, `siblings`, `path`, `routes`, `search` or `resolutions` find nothing, they say why and
suggest the next query: a table name that does not exist lists the similar (or all) table names, a method without
callers lists its other incoming edges, a symbol without siblings points to its callees that touch data, and a missing
path reports a path in the opposite direction when there is one.

```text
$ cg siblings StockService::reserve --db out/graph.db
target: Services\StockService::reserve
no siblings found for Services\StockService::reserve: its class has no parent class, interface or trait shared with other classes; it touches no table, column, config, env key or connection directly; all 2 of its callees are in its own class, which co-caller matching skips.
try: siblings('Services\StockService::reserveLocal') (a callee that touches data); siblings('Services\StockService::reserveFromWarehouse') (a callee that touches data); impact('Services\StockService::reserve') for its callers and entry points; downstream('Services\StockService::reserve') for the tables, config and connections it reaches
```

## Partial answers

`routes`, `impact`, `reaches` and `tests` say when their answer could be partial: a blind spot (a route or handler
registration cg does not model) or a file that is not indexed in a language of the answer. Totals then read as indexed
totals and a `coverage note:` line names the place to check with text search; `--json` output carries the same facts
in a `completeness` object. Complete answers keep the usual wording. See [completeness.md](completeness.md).

```text
$ cg routes --db out/shop.db
all routes: 1 indexed (possibly more: 1 unmodelled route registration)
…
coverage note: 1 route registration cg does not model (Django urlpatterns built by a function call, comprehension or loop: shop/urls.py:9). There, use your normal search and file reading (an empty cg answer is not proof of absence).
```

## Routes and guards

`routes` joins three facts per route: its guards, what it reaches, and who calls it.

- **Guards** come from Laravel route and group middleware, Nest `@UseGuards` / `@UseInterceptors` / `@UsePipes`
  (controller and method level, plus `APP_GUARD` providers and `useGlobalGuards` / `useGlobalInterceptors`), Express / Fastify / Koa / Hono route and router
  middleware, Next.js `middleware.ts` matchers and handler wrappers, django-ninja `auth=`, Django view decorators
  (`login_required`, `permission_required`, `user_passes_test`, `staff_member_required`, `method_decorator`), access
  mixins (`LoginRequiredMixin`, `PermissionRequiredMixin`, …) and DRF `permission_classes` / `authentication_classes`.
- **Auth** is decided in this order: a project pattern (`.cg.yaml` `auth.extra_patterns`, `--auth-pattern REGEX`),
  the framework preset's list of auth guards and of guards that are not auth (`csrf_protect`, `ThrottlerGuard`,
  `AllowAny`), then the auth name pattern (tokens such as `auth`, `login`, `jwt`, `token`, `session`, `permission`,
  `ApiKey`, …). Each auth guard records the rule that matched (`auth_by`: `preset laravel`, `name pattern`,
  `project pattern`), and the summary counts them (`auth guards by source: …`). Presets:
  [configuration.md](configuration.md#framework-presets).
- **Scope:** `--writes` (any table) or `--writes TABLE`, and/or `--reaches SPEC...` (any node spec). Without a scope
  every route is listed.
- Laravel kernel middleware and Django's `MIDDLEWARE` setting apply to every route and are not repeated per route.
- **Filters:** `--unguarded` keeps routes without an auth guard; `--missing NAME` keeps routes without a guard whose
  name contains NAME.

```text
$ cg routes --writes books --missing auth:api --db out/graph.db
routes reaching a write to books: 3 of 9 routes | filter: missing a guard matching 'auth:api' -> 2
auth guard: 0 with, 2 without (auth = a framework preset auth guard or a name matching the auth pattern)

POST /v1/admin/books  @bookstore-api/routes/api.php:23  NO AUTH
    guards: (none)
    writes books via Http\Controllers\Admin\BookController::store conf=resolved  ROUTES_TO@api.php:23 → WRITES_COLUMN@BookController.php:15~r → column:books.store_id

PUT /v1/admin/books/{id}  @bookstore-api/routes/api.php:24  NO AUTH
    guards: (none)
    writes books via Http\Controllers\Admin\BookController::update conf=resolved  ROUTES_TO@api.php:24 → WRITES_COLUMN@BookController.php:30~r → column:books.title
```

`cg search auth` lists the same guard names with the routes that carry them:

```text
$ cg search auth --db out/graph.db
middleware / guards / auth matching 'auth' (route attributes): 1 name(s) on 1 route(s)
  auth:api  (middleware) on 1 route(s): POST /v1/orders
```


## Full usage

Generated from `--help`.

### `index`

```
usage: python -m codegraph.cli index [-h] --db DB [--name NAME] [--scip SCIP]
                       [--gates GATES] [--python-root DIR] [--no-apps]
                       [--include-generated]
                       root

positional arguments:
  root

options:
  -h, --help           show this help message and exit
  --db DB
  --name NAME
  --scip SCIP
  --gates GATES        gate scenarios JSON (e.g.
                       examples/bookstore.gates.json)
  --python-root DIR    Python source root, relative to ROOT (repeatable);
                       replaces detection and python.source_roots in .cg.yaml
  --no-apps            index ROOT as one project although its .cg.yaml lists
                       monorepo apps
  --include-generated  also index generated, copied and vendored files
                       (labelled attrs.generated); default: excluded and
                       listed by `cg coverage`
```

### `config`

```
usage: python -m codegraph.cli config [-h] [--python-root DIR] [--gates GATES]
                        [--auth-pattern AUTH_PATTERN] [--plans-dir PLANS_DIR]
                        [--presets PRESETS] [--include-generated] [--json]
                        {show,validate} [root]

positional arguments:
  {show,validate}
  root                  indexed root (or, for validate, a config file)

options:
  -h, --help            show this help message and exit
  --python-root DIR     as for index
  --gates GATES         as for index
  --auth-pattern AUTH_PATTERN
                        as for routes
  --plans-dir PLANS_DIR
                        as for plan / serve
  --presets PRESETS     as for serve
  --include-generated   as for index
  --json                the effective configuration as JSON
```

### `starters`

```
usage: python -m codegraph.cli starters [-h] --db DB [--json]

options:
  -h, --help  show this help message and exit
  --db DB
  --json
```

### `coverage`

```
usage: python -m codegraph.cli coverage [-h] --db DB [--json] [--details] [--all-files]

options:
  -h, --help   show this help message and exit
  --db DB
  --json
  --details    the full report: file lists (the first 5 per bucket), fix
               hints, syntax error lines, Python source roots (default: a
               short summary)
  --all-files  the full report with every file per bucket, excluded files too
```

### `link`

```
usage: python -m codegraph.cli link [-h] --backend BACKEND --frontend FRONTEND --db DB
                      [--backend-name BACKEND_NAME]
                      [--frontend-name FRONTEND_NAME] [--report REPORT]

options:
  -h, --help            show this help message and exit
  --backend BACKEND
  --frontend FRONTEND
  --db DB
  --backend-name BACKEND_NAME
  --frontend-name FRONTEND_NAME
  --report REPORT       write <prefix>.json/.md match report
```

### `reaches`

```
usage: python -m codegraph.cli reaches [-h] --db DB [--json]
                         [--min-confidence {heuristic,resolved,exact}]
                         [--no-paths] [--max-depth MAX_DEPTH] [--gate GATE]
                         [--platform PLATFORM]
                         specs [specs ...]

positional arguments:
  specs

options:
  -h, --help            show this help message and exit
  --db DB
  --json
  --min-confidence {heuristic,resolved,exact}
  --no-paths
  --max-depth MAX_DEPTH
  --gate GATE           gate scenario for live/gated split (default: the one
                        indexed; 'none' to disable)
  --platform PLATFORM   only code built for this target (windows, linux,
                        macos, ios, android, web; see docs/platforms.md)
```

### `impact`

```
usage: python -m codegraph.cli impact [-h] --db DB [--json]
                        [--min-confidence {heuristic,resolved,exact}]
                        [--no-paths] [--max-depth MAX_DEPTH] [--gate GATE]
                        [--platform PLATFORM] [--plans-dir PLANS_DIR]
                        spec

positional arguments:
  spec

options:
  -h, --help            show this help message and exit
  --db DB
  --json
  --min-confidence {heuristic,resolved,exact}
  --no-paths
  --max-depth MAX_DEPTH
  --gate GATE           gate scenario for live/gated split (default: the one
                        indexed; 'none' to disable)
  --platform PLATFORM   only code built for this target (windows, linux,
                        macos, ios, android, web; see docs/platforms.md)
  --plans-dir PLANS_DIR
                        also list external clients from the snapshot files in
                        this directory (e.g. examples/plans)
```

### `downstream`

```
usage: python -m codegraph.cli downstream [-h] --db DB [--json]
                            [--min-confidence {heuristic,resolved,exact}]
                            [--no-paths] [--max-depth MAX_DEPTH] [--gate GATE]
                            [--platform PLATFORM]
                            spec

positional arguments:
  spec

options:
  -h, --help            show this help message and exit
  --db DB
  --json
  --min-confidence {heuristic,resolved,exact}
  --no-paths
  --max-depth MAX_DEPTH
  --gate GATE           gate scenario for live/gated split (default: the one
                        indexed; 'none' to disable)
  --platform PLATFORM   only code built for this target (windows, linux,
                        macos, ios, android, web; see docs/platforms.md)
```

### `path`

```
usage: python -m codegraph.cli path [-h] --db DB
                      [--min-confidence {heuristic,resolved,exact}]
                      [--platform PLATFORM]
                      src dst

positional arguments:
  src
  dst

options:
  -h, --help            show this help message and exit
  --db DB
  --min-confidence {heuristic,resolved,exact}
  --platform PLATFORM   only code built for this target (windows, linux,
                        macos, ios, android, web; see docs/platforms.md)
```

### `writers`

```
usage: python -m codegraph.cli writers [-h] --db DB [--json]
                         [--min-confidence {heuristic,resolved,exact}]
                         [--no-paths] [--max-depth MAX_DEPTH] [--gate GATE]
                         spec

positional arguments:
  spec

options:
  -h, --help            show this help message and exit
  --db DB
  --json
  --min-confidence {heuristic,resolved,exact}
  --no-paths
  --max-depth MAX_DEPTH
  --gate GATE           gate scenario for live/gated split (default: the one
                        indexed; 'none' to disable)
```

### `readers`

```
usage: python -m codegraph.cli readers [-h] --db DB [--json]
                         [--min-confidence {heuristic,resolved,exact}] [--no-paths]
                         [--max-depth MAX_DEPTH] [--gate GATE]
                         spec

positional arguments:
  spec

options:
  -h, --help            show this help message and exit
  --db DB
  --json
  --min-confidence {heuristic,resolved,exact}
  --no-paths
  --max-depth MAX_DEPTH
  --gate GATE           gate scenario for live/gated split (default: the one
                               indexed; 'none' to disable)
```

### `roundtrip`

A heuristic check for state that round-trips through a lossy transform (#88 phase 2).

For each write site of a stored property, `roundtrip` reports whether the written expression passes through a lossy
call. Lossy calls are:
- clamp, `coerceIn`, min / max, round / floor / ceil / trunc, truncating casts (`Int(...)`, `toInt()`), quantize,
  and `fit*` / `gamut*` / `snap*` / `limit*` style names;
- names from `.cg.yaml` `lossy: [...]`;
- functions whose doc comment contains `@cg-lossy`.

Data flow is followed through the statement, a local assigned earlier in the same function, and one hop through
the direct callers when the written value is a parameter.

For each read site, it reports whether the read seeds UI state. Seeds are:
- an initializer, constructor, `init` block or property initializer;
- `onAppear` / `.task`;
- `remember` / `mutableStateOf`;
- `useState(initial)` / `useRef`;
- `mounted` / `onMounted`;
- `State(initialValue:)`;
- a view constructed on the same line.

A finding is a lossy write plus a seeding read, as a path: write → lossy call → property → read → seed, each with
`file:line`. A read inside the write statement itself is not paired with it. When the lossy bounds are known
(`clamp(v, 0, 10)`, or the numbers in a project `fitToGamut` body), wider ranges in the reader's file are listed as
"un-narrowed range", for example `Slider(in: 0...100)`, `valueRange = 0f..100f` or `min={0} max={100}`.

Everything is labelled `heuristic`, in text and in JSON (`confidence`), and nothing is added to the graph. The MCP
tool is `roundtrip(prop)`.

```
usage: cg roundtrip [-h] --db DB [--json] [--tests] spec

positional arguments:
  spec

options:
  -h, --help  show this help message and exit
  --db DB
  --json
  --tests     include test code's reads and writes
```

### `lint`

`lint async-state` is #88 phase 3. Two rules are implemented: `stale-async-result` and `two-writers`.

It flags a write of stored or UI state (a WRITES_PROP edge) that meets all of these:
- it is inside an async block: Swift `Task { }` or an `async` func, Kotlin `launch { }` / `async { }`, a React
  `useEffect` callback, a JS / TS `async` function or `.then(...)`, or a Python `async def` / `create_task`;
- it comes after an `await` (Kotlin: `withContext`, `delay`, `.await()`, `.first()`);
- it writes the awaited result: a name bound on the await line or derived from one;
- between the await and the write there is no cancellation check (`Task.isCancelled`, `checkCancellation`,
  `isActive`, `ensureActive`, a `cancelled` / `ignore` / `stale` / `mounted` flag, `signal.aborted`);
- there is also no comparison against a token, ID or generation captured before the await;
- the awaited request depends on an input (a parameter, prop, state or loop variable), so a newer request with other
  inputs can overtake it; a fixed `client.post("/auth.config")` does not;
- the block is not wrapped in a single-flight helper (`bundleAsync`, `dedupe`, `debounce`, `throttle`, `once`).

Writes are skipped when they go to an object the function just fetched (`doc.x = …` with a local receiver) and when
they are in test paths (unless `--tests` is given).

`two-writers` flags state that is written with a real value from both sides:
- by lifecycle code: an initializer or `init` block, `onAppear`, `.task` before its first await, `useEffect`,
  `onMounted`, `LaunchedEffect` or `viewDidLoad`;
- by async code after an await, or by a completion or subscription callback (`.sink`, `.then`, `.collect`,
  `completion: {`).

Defaults and flag resets (`= []`, `setLoading(true)`, `setError(null)`) and in-place mutations are not competing
values, so they are skipped.

Findings are labelled `heuristic` and nothing is added to the graph. The MCP tool is `lint_async_state`. The rules
incomplete cache key and echo suppression are not implemented yet.

```
usage: cg lint [-h] --db DB [--json] [--tests] {async-state}

positional arguments:
  {async-state}

options:
  -h, --help     show this help message and exit
  --db DB
  --json
  --tests        include test code
```

### `siblings`

```
usage: python -m codegraph.cli siblings [-h] --db DB [--json]
                          [--min-confidence {heuristic,resolved,exact}]
                          [--no-paths] [--max-depth MAX_DEPTH] [--gate GATE]
                          spec

positional arguments:
  spec

options:
  -h, --help            show this help message and exit
  --db DB
  --json
  --min-confidence {heuristic,resolved,exact}
  --no-paths
  --max-depth MAX_DEPTH
  --gate GATE           gate scenario for live/gated split (default: the one
                        indexed; 'none' to disable)
```

### `routes`

```
usage: python -m codegraph.cli routes [-h] --db DB [--writes [TABLE]]
                        [--reaches SPEC [SPEC ...]] [--missing NAME]
                        [--unguarded] [--auth-pattern AUTH_PATTERN]
                        [--min-confidence {heuristic,resolved,exact}]
                        [--max-items MAX_ITEMS] [--no-paths] [--json]
                        [--platform PLATFORM]

options:
  -h, --help            show this help message and exit
  --db DB
  --writes [TABLE]      routes reaching a DB write (any table, or TABLE)
  --reaches SPEC [SPEC ...]
                        routes reaching any of these nodes (table, column,
                        connection:, env:, Class.method)
  --missing NAME        keep routes with no guard whose name contains NAME
                        (e.g. auth:api, ApiKeyGuard)
  --unguarded           keep routes with no auth guard (framework presets, the
                        auth name pattern, .cg.yaml auth.extra_patterns and
                        --auth-pattern)
  --auth-pattern AUTH_PATTERN
                        extra regex for guard names that count as auth
  --min-confidence {heuristic,resolved,exact}
  --max-items MAX_ITEMS
  --no-paths
  --json
  --platform PLATFORM   only code built for this target (windows, linux,
                        macos, ios, android, web; see docs/platforms.md)
```

### `search`

```
usage: python -m codegraph.cli search [-h] --db DB [--kind KIND] [--limit LIMIT] [--json]
                        [--platform PLATFORM]
                        name

positional arguments:
  name

options:
  -h, --help           show this help message and exit
  --db DB
  --kind KIND
  --limit LIMIT
  --json
  --platform PLATFORM  only code built for this target (windows, linux, macos,
                       ios, android, web; see docs/platforms.md)
```

### `node`

```
usage: python -m codegraph.cli node [-h] --db DB [--json]
                      [--min-confidence {heuristic,resolved,exact}]
                      [--no-paths] [--max-depth MAX_DEPTH] [--gate GATE]
                      spec

positional arguments:
  spec

options:
  -h, --help            show this help message and exit
  --db DB
  --json
  --min-confidence {heuristic,resolved,exact}
  --no-paths
  --max-depth MAX_DEPTH
  --gate GATE           gate scenario for live/gated split (default: the one
                        indexed; 'none' to disable)
```

### `stats`

```
usage: python -m codegraph.cli stats [-h] --db DB [--json]
                       [--min-confidence {heuristic,resolved,exact}]
                       [--no-paths] [--max-depth MAX_DEPTH] [--gate GATE]

options:
  -h, --help            show this help message and exit
  --db DB
  --json
  --min-confidence {heuristic,resolved,exact}
  --no-paths
  --max-depth MAX_DEPTH
  --gate GATE           gate scenario for live/gated split (default: the one
                        indexed; 'none' to disable)
```

### `api-calls`

```
usage: python -m codegraph.cli api-calls [-h] --db DB [--json]
                           [--min-confidence {heuristic,resolved,exact}]
                           [--no-paths] [--max-depth MAX_DEPTH] [--gate GATE]
                           spec

positional arguments:
  spec                  all | unmatched | a substring | a * glob ('GET
                        /v1/*/orders*', '*useOrders*')

options:
  -h, --help            show this help message and exit
  --db DB
  --json
  --min-confidence {heuristic,resolved,exact}
  --no-paths
  --max-depth MAX_DEPTH
  --gate GATE           gate scenario for live/gated split (default: the one
                        indexed; 'none' to disable)
```

### `channels`

```
usage: python -m codegraph.cli channels [-h] --db DB [--json] [--no-source] [pattern]

positional arguments:
  pattern      channel pattern or concrete name (orders.{id}, orders.42,
               orders.*); omit to list all

options:
  -h, --help   show this help message and exit
  --db DB
  --json
  --no-source
```

### `bridges`

```
usage: python -m codegraph.cli bridges [-h] --db DB [--json]
                         [--protocol {capacitor,react-native,flutter,flutter-event,electron-ipc,electron-preload,tauri}]
                         [--unmatched]
                         [pattern]

positional arguments:
  pattern               endpoint name, substring or glob (Echo#echo, Echo,
                        samples.flutter.dev/*); omit to list all

options:
  -h, --help            show this help message and exit
  --db DB
  --json
  --protocol {capacitor,react-native,flutter,flutter-event,electron-ipc,electron-preload,tauri}
  --unmatched           only endpoints with a check: missing on a platform, no
                        receiver, no sender, external
```

### `tools`

```
usage: cg tools [-h] --db DB [--json] [--framework FRAMEWORK] [--agent AGENT]
                [--unmatched] [--max-items MAX_ITEMS]
                [pattern]

positional arguments:
  pattern               tool name, substring or glob

options:
  -h, --help            show this help message and exit
  --db DB
  --json
  --framework FRAMEWORK
                        mcp, openai, anthropic, langchain, openai-agents,
                        llamaindex, custom
  --agent AGENT         only tools this agent offers (name or glob)
  --unmatched           only tools with a check (no_receiver, no_sender,
                        name_collision)
  --max-items MAX_ITEMS
```

### `protocols`

```
usage: python -m codegraph.cli protocols [-h] --db DB [--json] [--protocol PROTOCOL]
                    [--side {send,receive}] [--max-items MAX_ITEMS]
                    [--unmatched]
                    [pattern]

positional arguments:
  pattern               endpoint name, id, substring or glob (`orders.*`,
                        `http:GET /api/*`); omit for the summary

options:
  -h, --help            show this help message and exit
  --db DB
  --json
  --protocol PROTOCOL   one protocol (http, pusher, nest-rpc, bull, laravel-
                        queue, socketio, mqtt, capacitor, ...)
  --side {send,receive}
  --max-items MAX_ITEMS
  --unmatched           only endpoints with a check (no_receiver, no_sender,
                        ambiguous, schema_mismatch, unguarded) or an external
                        peer
```

### `external`

```
usage: cg external [-h] --db DB [--json] [--protocol PROTOCOL]
                   [--source SOURCE] [--tls-off] [--max-items MAX_ITEMS]
                   [pattern]

positional arguments:
  pattern               external id, substring or glob (`external:postgres:*`,
                        `redis`)

options:
  -h, --help            show this help message and exit
  --db DB
  --json
  --protocol PROTOCOL   postgres, mysql, redis, smtp, amqp, mongodb, ldap,
                        ssh, ftp, s3, https, ...
  --source SOURCE       address or credential source: literal, env, env-
                        example, compose, config
  --tls-off             only systems known to be reached without TLS
  --max-items MAX_ITEMS
```

### `tests`

```
usage: python -m codegraph.cli tests [-h] --db DB [--json] [--no-paths]
                [--min-confidence {heuristic,resolved,exact}]
                [--max-depth MAX_DEPTH] [--unit-only]
                [--exclude-root EXCLUDE_ROOT] [--through-roots]
                spec

positional arguments:
  spec                  Class.method or Class::method (either separator, any
                        language), Class, route:VERB /uri, `VERB /path`,
                        /path, table.column ...

options:
  -h, --help            show this help message and exit
  --db DB
  --json
  --no-paths
  --min-confidence {heuristic,resolved,exact}
  --max-depth MAX_DEPTH
                        transitive tests at most N hops from the target (0:
                        any depth)
  --unit-only           leave out UI / snapshot / screenshot tests
  --exclude-root EXCLUDE_ROOT
                        a symbol transitive paths must not run through
                        (repeatable)
  --through-roots       keep paths through app entry points (@main, App.body,
                        MainActivity)
```

### `parity`

```
usage: python -m codegraph.cli parity [-h] --db DB --against AGAINST [--map MAP] [--no-fuzzy]
                 [--strip-prefix STRIP_PREFIX] [--structure] [--no-learn]
                 [--write-map WRITE_MAP] [--json] [--max-items MAX_ITEMS]

options:
  -h, --help            show this help message and exit
  --db DB               source graph
  --against AGAINST     target graph
  --map MAP             JSON file {"source name": "target name"} for renames
  --no-fuzzy            no fuzzy (shortened / plural word) name matches
  --strip-prefix STRIP_PREFIX
                        a name prefix one side adds (`Vault` in
                        VaultAddEditState for AddEditState), ignored when
                        matching (repeatable)
  --structure           also pair symbols whose names differ by what they use:
                        shared strings, localization keys, endpoints and
                        already-paired callees, plus rename rules learned from
                        the pairs found (#93); matches carry their evidence
  --no-learn            with --structure: no learned rename rules
  --write-map WRITE_MAP
                        with --structure: write the inferred pairs as a --map
                        JSON file to review
  --json
  --max-items MAX_ITEMS
```

### `platforms`

```
usage: python -m codegraph.cli platforms [-h] --db DB [--target TARGET]
                           [--kind {variants,api_surface,missing_callee}]
                           [--max-items MAX_ITEMS] [--json]
                           [{summary,divergence}]

positional arguments:
  {summary,divergence}

options:
  -h, --help            show this help message and exit
  --db DB
  --target TARGET       divergence: only findings that affect this target
  --kind {variants,api_surface,missing_callee}
                        divergence: one finding kind
  --max-items MAX_ITEMS
  --json
```

See [platforms.md](platforms.md).

### `resolutions`

```
usage: python -m codegraph.cli resolutions [-h] --db DB [--within WITHIN] [--no-client]
                             [--json]
                             concept

positional arguments:
  concept

options:
  -h, --help       show this help message and exit
  --db DB
  --within WITHIN  substring filter on the owning function fqn (e.g. Report)
  --no-client
  --json
```

### `plan`

```
usage: python -m codegraph.cli plan [-h] [--db DB] [--plans-dir PLANS_DIR] [--verify]
                      [--json] [-o OUT] [--max-items MAX_ITEMS] [--summary]
                      {list,load,validate,check,baseline} [name]

positional arguments:
  {list,load,validate,check,baseline}
  name

options:
  -h, --help            show this help message and exit
  --db DB
  --plans-dir PLANS_DIR
  --verify              after implement + re-index: planned nodes/edges must
                        now exist
  --json
  -o, --out OUT         also write the report to this file
  --max-items MAX_ITEMS
  --summary             compact report: counts per section and check plus the
                        top --max-items items
```

### `viz-export`

```
usage: python -m codegraph.cli viz-export [-h] --db DB -o OUT [--sinks SINKS]
                            [--min-confidence {heuristic,resolved,exact}]
                            {reaches,impact,downstream,path} specs [specs ...]

positional arguments:
  {reaches,impact,downstream,path}
  specs

options:
  -h, --help            show this help message and exit
  --db DB
  -o, --out OUT
  --sinks SINKS         downstream sink kinds, comma separated (e.g.
                        table,column)
  --min-confidence {heuristic,resolved,exact}
```

### `viz-plan`

```
usage: python -m codegraph.cli viz-plan [-h] --db DB -o OUT [--plans-dir PLANS_DIR] name

positional arguments:
  name

options:
  -h, --help            show this help message and exit
  --db DB
  -o, --out OUT
  --plans-dir PLANS_DIR
```

### `serve`

```
usage: python -m codegraph.cli serve [-h] --db DB [--port PORT] [--host HOST]
                       [--plans-dir PLANS_DIR] [--presets PRESETS]

options:
  -h, --help            show this help message and exit
  --db DB
  --port PORT
  --host HOST
  --plans-dir PLANS_DIR
  --presets PRESETS     JSON list of canned queries for the starter cards
                        (default: viz.presets in .cg.yaml, then the sample
                        presets that resolve, then the starter queries)
```

### `clean`

```
usage: python -m codegraph.cli clean [-h] [--all] [--extractors] [--stale] [--db DB] [--dry-run]
                [--json]
                [root]

positional arguments:
  root          project root: remove the cache entries of this project and of
                every project indexed below it

options:
  -h, --help    show this help message and exit
  --all         empty the whole cache root (keeps the extractors)
  --extractors  with --all: also remove the extractor installs
  --stale       only entries of older cache versions / layouts and orphaned
                .tmp / .lock files
  --db DB       also delete this graph DB and its -wal / -shm files
  --dry-run     list what would be removed, delete nothing
  --json
```

cg keeps everything it builds outside the indexed projects, under one **cache root**: `$CODEGRAPH_CACHE`, else
`$CODEGRAPH_CACHE_DIR` (the older name, still read), else `%LOCALAPPDATA%\codegraph` on Windows, else
`$XDG_CACHE_HOME/codegraph`, else `~/.cache/codegraph`. Every cache user (extractor installs, SCIP outputs, the TS and
Dart facts caches, the Swift build directory, rust-analyzer configs) takes its directory from this one lookup.

| kind (`doctor`) | path below the root | what |
|---|---|---|
| `extractors` | `extractors/<language>-<lock hash>/` | npm / Composer / dart pub installs of the extractors (`cg setup`) |
| `scip` | `scip/<indexer>-v<N>-<project key>-<key>.scip` and `.lock` | SCIP output of the exact layers (Rust, C/C++, Kotlin) |
| `rust-analyzer` | `scip/ra-config-<project key>-<hash>.json` | the config file rust-analyzer runs with |
| `swift-build` | `swift-build/<build key>/` | `swift build --enable-index-store` output (`CODEGRAPH_SWIFT_INDEX=1`) |
| `ts`, `dart` | `ts/<project key>-v<N>-<fingerprint>.json` | extractor facts, one entry per project |
| `projects` | `projects/<project key>` | the project root a key stands for |

`v<N>` is the cache version and the project key the first 12 hex digits of the SHA-256 of the resolved project root.

- `cg clean ROOT` removes the entries of the project at ROOT and of every project indexed below it (a monorepo's
  apps), in every kind except `extractors`.
- `cg clean --stale` removes what no cg can read again: entries of an older cache version, names from an older cache
  layout (up to cg 0.7.1, SCIP outputs and TS / Dart facts carried no project key / cache version in their names), temporary files older
  than the indexer timeout (`CODEGRAPH_INDEXER_TIMEOUT` + 10 min), and lock files without their cache entry.
- `cg clean --all` empties the cache root but keeps the extractors; `--extractors` removes them too (the next index
  or `cg setup` installs them again).
- `--db DB` deletes the graph DB and its `-wal` / `-shm` / `-journal` files; a file that is not an SQLite database is
  refused. On its own, `--db` leaves the cache alone.
- `--dry-run` lists the paths and sizes and deletes nothing; `--json` prints the same as JSON.

Safety: nothing outside the cache root is deleted except an explicit `--db`; symlinks in the cache are removed, never
followed; a lock a running cg holds is skipped. When the cache root resolves to `/`, a drive root, `$HOME` or a
directory above `$HOME`, `cg clean` refuses with exit status 2 and deletes nothing.

### `detect`

```
usage: python -m codegraph.cli detect [-h] root

positional arguments:
  root

options:
  -h, --help  show this help message and exit
```
