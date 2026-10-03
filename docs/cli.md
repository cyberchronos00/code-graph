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
- `coverage --db DB [--json] [--all-files]`: which languages and files the index covers: parser mode (`exact`,
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
  page with the largest backend reach, the most-called functions). The visual view's preset menu offers them too.
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
- `search NAME [--kind K]`: nodes by name / FQN substring, plus the routes whose middleware, guard or auth names match.
- `writers TABLE`, `siblings SYMBOL`, `node SPEC`, `stats`: writers of a table, similar code, node details, counts.
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
  Expo modules, Flutter channels): JS / Dart senders, native receivers per platform, methods missing on a platform,
  without a receiver or implemented outside the repo. See [bridges.md](bridges.md).
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
- `Class::method`, `Class`, short or FQN: code symbols (suffix match).
- `Sub.method` / `Sub::method` for a method `Sub` inherits without redefining it: resolves through the class's
  ancestors (EXTENDS / IMPLEMENTS / trait use, nearest first) to the definition, and the answer says so
  (`B.run -> inherited from Base.run`). The callers are those of the inherited definition (calls are not narrowed to
  `Sub` instances); of its overrides only those in `Sub` and its subclasses are followed.
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
usage: python -m codegraph.cli coverage [-h] --db DB [--json] [--all-files]

options:
  -h, --help   show this help message and exit
  --db DB
  --json
  --all-files  list every file per bucket (default: the first 5), excluded
               files too
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
                        connection:, env:, Class::method)
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
                         [--protocol {capacitor,react-native,flutter,flutter-event}]
                         [--unmatched]
                         [pattern]

positional arguments:
  pattern               endpoint name, substring or glob (Echo#echo, Echo,
                        samples.flutter.dev/*); omit to list all

options:
  -h, --help            show this help message and exit
  --db DB
  --json
  --protocol {capacitor,react-native,flutter,flutter-event}
  --unmatched           only endpoints with a check: missing on a platform, no
                        receiver, no sender, external
```

### `tests`

```
usage: python -m codegraph.cli tests [-h] --db DB [--json] [--no-paths]
                       [--min-confidence {heuristic,resolved,exact}]
                       spec

positional arguments:
  spec                  Class::method, Class, route:VERB /uri, `VERB /path`,
                        /path, table.column ...

options:
  -h, --help            show this help message and exit
  --db DB
  --json
  --no-paths
  --min-confidence {heuristic,resolved,exact}
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
  --presets PRESETS     JSON list of canned queries for the preset menu
                        (default: viz.presets in .cg.yaml, then the sample
                        presets that resolve, then the starter queries)
```

### `detect`

```
usage: python -m codegraph.cli detect [-h] root

positional arguments:
  root

options:
  -h, --help  show this help message and exit
```
