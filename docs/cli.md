# CLI reference

`cg <command>` is `python -m codegraph.cli <command>`. Query commands need `--db`. Flags not listed here: `cg <command> -h`.

## Commands at a glance

| Command | One line | Detail |
|---|---|---|
| `index ROOT --db DB` | Build the graph. Stats JSON on stdout; coverage on stderr. `--name`, `--gates`, `--scip` (repeatable). Missing toolchain skips that language. Invalid `.cg.yaml` exits 2. | [configuration](configuration.md#project-file) |
| `detect ROOT` | Languages and frameworks under ROOT, no index. | `cg detect -h` |
| `doctor [ROOT]` | Versions, extractors, and exact / heuristic / unavailable per language. ROOT limits this to the project. `--scip FILE` checks an index (repeatable). A module that fails to import exits 1. | [install](install.md) |
| `setup [LANG]...` | Install TypeScript / PHP / Dart extractors now (`--quiet`). Default: every language whose toolchain is present. | [install](install.md) |
| `setup --prune` | Remove extractor installs this cg does not use. `--dry-run` lists them. A held lock is kept. | [clean](#clean) |
| `config show\|validate [ROOT]` | Effective config and where each value comes from, or check `.cg.yaml` (exit 2 if invalid). Same flags as `index`, `routes`, and `serve` (`--no-apps` included). | [configuration](configuration.md#project-file) |
| `coverage --db DB` | One-line summary per repo and per language that is not fully indexed. `--details` / `--all-files` expand it. | [completeness](completeness.md) |
| `starters --db DB` | Starter queries, each with the matching command and MCP call. | [viz](viz.md) |
| `link --backend DB --frontend DB --db OUT` | Merge graphs and match client HTTP calls to routes. Two repos: `--backend` / `--frontend`. N repos: repeat `--repo NAME=DB[:role]`. `--report PREFIX` writes `.json` and `.md`. | [workspace](#workspace) |
| `reaches SPEC...` | Dependents, grouped by entry classification. On a base method, overrides are followed. | [specs](#query-targets-specs) |
| `impact SPEC` | Callers up to entry points. `--plans-dir` adds external snapshot clients. | [answer shape](#answer-shape) |
| `downstream SPEC` | Forward walk: page, composables, HTTP, routes, services, tables. | [specs](#query-targets-specs) |
| `path SRC DST` | One shortest evidence chain. No path: reason on stdout, exit 1. | [answer shape](#answer-shape) |
| `routes` | Guards, what the route reaches, and frontend callers on a linked graph. | [routes and guards](#routes-and-guards) |
| `search NAME` | Nodes by name or FQN substring, plus middleware / guard / auth names. `--kind`, `--limit`. | `cg search -h` |
| `snippet SPEC` | Source from the index: `path:start-end`, then numbered lines. `--context`, `--max-lines` (default 200). Ambiguous name lists candidates and exits nonzero. | [specs](#query-targets-specs) |
| `explore QUERY` | Question or spec → source, entry points, call paths, blast radius in one answer. `--budget`, `--json`. | [explore](#explore) |
| `node SPEC` | Location, fqn, platforms, attrs, and edges (one site, with a count). | `cg node -h` |
| `stats` | Project, languages, coverage line, node and edge counts. | `cg stats -h` |
| `writers SPEC` | Writers of a table, a column, or a stored property `Type.prop` (Swift, Kotlin, Python, TypeScript, PHP). | `cg writers -h` |
| `readers Type.prop` | Readers of a stored property (`READS_PROP`). | `cg readers -h` |
| `siblings SYMBOL` | Hierarchy, the same method on siblings, shared resources, co-callers. | `cg siblings -h` |
| `roundtrip Type.prop` | Heuristic lossy-write / UI-seed pairs. `--tests` includes test code. Nothing is written to the graph. | [heuristics](#heuristics) |
| `lint async-state` | Heuristic rules `stale-async-result`, `two-writers`, `incomplete-cache-key`, `echo-suppression`. `--rules` is a subset. `--tests` includes test code. | [heuristics](#heuristics) |
| `api-calls SPEC` | Client endpoints, call sites, request keys, matched route. SPEC is `all`, `unmatched`, a substring, or a `*` glob. | `cg api-calls -h` |
| `channels [PATTERN]` | Who can join, who publishes, who listens. `--no-source` hides source. | [channels](channels-and-tests.md#broadcast-channels) |
| `bridges [PATTERN]` | Web / native bridges and Electron / Tauri boundaries. `--protocol` (`capacitor`, `react-native`, `flutter`, `flutter-event`, `electron-ipc`, `electron-preload`, `tauri`), `--unmatched`. | [bridges](bridges.md) |
| `protocols [PATTERN]` | Summary per protocol, or one endpoint's senders, receivers, guards, and checks (`no_receiver`, `no_sender`, `ambiguous`, `schema_mismatch`, `unguarded`). `--side send\|receive`, `--listeners` (TCP / UDP binds). | [protocols](protocols.md) |
| `tools [PATTERN]` | LLM and MCP tools. `--framework` (`mcp`, `openai`, `anthropic`, `langchain`, `openai-agents`, `llamaindex`, `custom`), `--agent`, `--unmatched` (`no_receiver`, `no_sender`, `name_collision`). | [ai-tools](ai-tools.md) |
| `external [PATTERN]` | External systems, who uses them, address and credential source, TLS. `--protocol` (`postgres`, `mysql`, `redis`, `smtp`, `amqp`, `mongodb`, `ldap`, `ssh`, `ftp`, `s3`, `https`, …). `--source` is `literal`, `env`, `env-example`, `compose`, or `config`. `--tls-off`. | [external](external.md) |
| `tests SPEC` | Direct and transitive tests. `--unit-only`, `--exclude-root` (repeatable), `--through-roots`. A file or module spec covers every symbol in it. | [channels](channels-and-tests.md#tests) |
| `affected [FILES...]` | Tests and entry points a change reaches. `--base REF` uses only the touched lines; `--quiet` prints test files. | [affected](#affected) |
| `parity --db SRC --against TGT` | Symbols in SRC with no counterpart in TGT. `--map`, `--strip-prefix` (repeatable), `--no-fuzzy`. `--structure` also pairs by use; `--no-learn` and `--write-map` apply only with it. | [parity](parity.md) |
| `platforms [summary\|divergence]` | Targets and tagged symbols, or divergence findings. `--target`. `--kind` is `variants`, `api_surface`, `missing_callee`, or `missing_callee_tests`. | [platforms](platforms.md) |
| `resolutions CONCEPT` | Where a value is resolved, its fallbacks, and whether the client sends it. `--within`, `--no-client`. | [value facts](value-facts.md) |
| `plan ACTION [NAME]` | `list`, `load`, `validate`, `check`, `baseline`. `--verify`, `--summary`, `-o`. | [plans](plans.md) |
| `serve --db DB` | Local web UI. `--port`, `--host`, `--plans-dir`, `--presets`. | [viz](viz.md) |
| `viz-export MODE SPECS -o OUT` | Self-contained HTML. MODE is `reaches`, `impact`, `downstream`, or `path`. `--sinks`. | [viz](viz.md) |
| `viz-plan NAME -o OUT` | Self-contained HTML of a plan on the graph. `--plans-dir`. | [plans](plans.md) |
| `agents ACTION` | Opt-in block in agent guidance files. Previews and asks. `--mcp` also writes the `cg-mcp` server entry. | [mcp](mcp.md) |
| `install` / `uninstall` | Register or remove the cg MCP server (key `cg`) in a host config. `--host`, `--global`, `--dry-run`. | [install](#install) |
| `hooks install\|uninstall\|status` | Opt-in git hooks that refresh the index after commit, checkout and merge. Nothing is installed by default. | [hooks](#hooks) |
| `refresh ROOT --db DB` | Re-index when sources changed. `--name`, `--quiet`. Hooks run this in the background. | [hooks](#hooks) |
| `watch ROOT --db DB` | Re-index on save, debounced (`watchfiles` if installed, else polling). Ctrl-C stops. | [watch](#watch) |
| `clean [ROOT]` | Cache entries for one project, `--stale` ones, or `--all`. | [clean](#clean) |

## Common flags

| Flag | Where | Meaning |
|---|---|---|
| `--db DB` | every query, plus `index`, `link`, `serve`, viz | graph file |
| `--json` | most commands | one JSON document (`config show`: the effective config) |
| `--min-confidence {heuristic,resolved,exact}` | `reaches`, `impact`, `downstream`, `path`, `writers`, `readers`, `siblings`, `node`, `stats`, `api-calls`, `routes`, `tests`, `viz-export` | drop edges below this |
| `--max-depth N` | `reaches`, `impact`, `downstream`, `writers`, `readers`, `siblings`, `node`, `stats`, `api-calls`, `tests` | hop cap (`tests`: `0` is any depth) |
| `--no-paths` | the `--max-depth` commands, plus `routes` | omit evidence chains |
| `--gate GATE` | the `--max-depth` commands except `tests` | live/gated split; default the scenario that was indexed (`auto`); `none` disables |
| `--platform TARGET` | `reaches`, `impact`, `downstream`, `path`, `routes`, `search` | one build: `windows`, `linux`, `macos`, `ios`, `android`, `web`. The first line names the filter and how many conditions could not be evaluated. [platforms](platforms.md#queries) |
| `--max-items N` | list commands (`routes`, `protocols`, `tools`, `external`, `parity`, `platforms`, `plan`, …) | cap listed rows |

`node --json` is a list of `{node, out, in}`. `stats --json` is `{project, root, indexed_at, nodes, edges, nodes_by_kind, edges_by_kind, stats}`.

## Workspace

`--backend` and `--frontend` merge two graphs. `--repo` (repeatable) merges any number. The two forms cannot be combined. `role` is `backend`, `frontend` or `both` (default `both`).

```bash
cg link --backend out/api.db --frontend out/web.db --db out/graph.db
```

```bash
cg link --repo orders=out/orders.db:backend \
        --repo gateway=out/gateway.db:backend \
        --repo web=out/web.db:frontend --db out/graph.db
```

Every repo's client calls are matched to routes on every other server, so a backend may call another backend. A frontend `links:` list limits which servers that app calls ([apps and workspace](configuration.md#apps-and-workspace)). Channels, protocols, payload checks and entry tagging run once on the combined graph. Stats include one row per client/server pair plus totals.

An id that exists in two repos is stored as `repo:` plus the original id (`orders:class:Order`), and edges that used it are rewritten. `external:` and `endpoint:` ids stay a single node. Every other id is unchanged, so a two-repo link with no shared ids keeps the same ids. `orders:Order` and `orders:class:Order` select that repo; a plain `Order` matches every repo that defines it ([specs](#query-targets-specs)).

`cg index` on a workspace `.cg.yaml` writes one combined `--db` for every app ([apps and workspace](configuration.md#apps-and-workspace)).

## Query targets (specs)

| Form | Selects |
|---|---|
| `table.column`, `column:table.column` | a DB column |
| `table:NAME` | a DB table |
| `connection:NAME`, `connection:tenant_*` | a connection from `config/database.php`, plus dynamic `Config::set('database.connections.…')` |
| `env:KEY`, `config:dotted.key` | an env or config key |
| `Class.method`, `Class::method`, `Class`, FQN | a symbol, suffix match |
| `repo:Class.method`, `repo:class:Order` | that symbol in one repo of a combined graph (`orders:Order.total`). A plain name matches every repo |
| `page:/reports/:id` | a Nuxt page by route |
| `app/pages/x.vue`, `app/composables/useX.ts` | a TS module or Vue SFC (repo-relative suffix) |
| `useX`, `useX.fn`, `fn` | a TS composable, store, or function |
| `src/app.ts#listOrders`, `svc.ts#OrderService.create` | a TS / JS / Vue symbol in one file (`file#name~2` is the second hit; `function:src/app.ts#listOrders` is the full id) |
| `http:GET /v1/…`, `route:GET /v1/…` | a client endpoint or a backend route (`*` glob) |
| `channel:orders.{order}`, `channel_sub:orders.{id}` | a broadcast channel or a client subscription |
| `test:tests/Feature/OrderTest.php::…` | a test case |
| `request_key:timezone`, `setting:reports.timezone` | a value fact ([value facts](value-facts.md)) |
| `crate::mod::Type::method`, `mod:path`, `feature:`, `cfg:`, `define:`, `unsafe:` | Rust / C / C++ ([native](native.md#query-specs)) |

- Either separator works. A spec that misses is retried with the other. Swift, Kotlin, Python, TS, and Dart fqns use `.`; PHP, Rust, and C++ use `::`.
- `Sub.method` for a method `Sub` inherits and does not redefine resolves to that definition (`B.run -> inherited from Base.run`; `--json` has fqns). Callers are the definition's, narrowed by receiver type (`attrs.recv`): a receiver that cannot be a `Sub` is left out and counted (`callers narrowed to B: 2 of 6 calls on other classes left out`). Unknown receivers, and ancestry the graph does not know (`extends mix(Base)`), stay. `reaches` and `tests` narrow the same way. Only overrides in `Sub` and its subclasses are followed.
- When `Sub` overrides the method, callers of the base (`via base`) drop calls whose receiver cannot be a `Sub` (`override_narrowed` in JSON).
- Several specs in one `reaches` call are unioned.

## Answer shape

- `index` leaves generated, copied, and vendored files out unless `--include-generated` (labelled `attrs.generated`; [generated](generated.md)). `--python-root` (repeatable) replaces detection and `python.source_roots`. Workspace `apps` are indexed into one combined graph unless `--no-apps` ([apps and workspace](configuration.md#apps-and-workspace)). `--scip` is repeatable. Starters that miss a 20s budget are named in `starters_skipped`.
- `impact` marks a held function `(ref: collection | callback | assignment | decorator)` and a dispatch or plugin list `(call through a collection)`. Override lines are `overrides: Base.m` and `overridden by: A.m, B.m`. The base is not a caller of its override; a call of the base declaration is `(via base Base.m)`. `impact` on a base also lists callers of the overrides, `(via override A.m +1)`.
- `path` to `table:` with no table edge ends at the column that is read or written. A hop that passes keys the next request never sends adds `note: sent but not forwarded: …`.
- `api-calls` folds a runtime or env base URL (`(base {runtimeConfig.apiBase} = …)`). Endpoints only tests call are marked `(called from tests only)`.

## Empty results

`impact`, `writers`, `siblings`, `path`, `routes`, `search`, and `resolutions` say why a miss happened and name a next query: a missing table lists similar names, a method with no callers lists other incoming edges, a symbol with no siblings points at callees that touch data, and a missing path reports the opposite direction when one exists.

```text
$ cg siblings StockService::reserve --db out/graph.db
target: Services\StockService::reserve
no siblings found for Services\StockService::reserve: its class has no parent class, interface or trait shared with other classes; it touches no table, column, config, env key or connection directly; all 2 of its callees are in its own class, which co-caller matching skips.
try: siblings('Services\StockService::reserveLocal') (a callee that touches data); siblings('Services\StockService::reserveFromWarehouse') (a callee that touches data); impact('Services\StockService::reserve') for its callers and entry points; downstream('Services\StockService::reserve') for the tables, config and connections it reaches
```

## Partial answers

`routes`, `impact`, `reaches`, and `tests` mark a partial answer: a route or handler registration cg does not model, or a file in the answer's language that is not indexed. Totals then read as indexed totals. A `coverage note:` names where to search by hand. `--json` carries the same facts in `completeness`. A complete answer keeps the usual wording. [completeness](completeness.md)

```text
$ cg routes --db out/shop.db
all routes: 1 indexed (possibly more: 1 unmodelled route registration)
coverage note: 1 route registration cg does not model (Django urlpatterns built by a function call, comprehension or loop: shop/urls.py:9). There, use your normal search and file reading (an empty cg answer is not proof of absence).
```

## Routes and guards

`routes` joins guards, what the route reaches, and who calls it. One evidence chain per route. On a combined graph, frontend callers are included.

- **Guards** come from Laravel route and group middleware, Nest `@UseGuards` / `@UseInterceptors` / `@UsePipes` (plus `APP_GUARD` and global guards / interceptors), Express / Fastify / Koa / Hono, Next.js `middleware.ts`, django-ninja `auth=`, Django view decorators and access mixins, and DRF `permission_classes` / `authentication_classes`.
- **Scope.** `--writes` or `--writes TABLE`, and/or `--reaches SPEC...`. No scope lists every route.
- **Auth,** in order: `--auth-pattern` / `.cg.yaml` `auth.extra_patterns`, the preset's auth guards (not `csrf_protect`, `ThrottlerGuard`, `AllowAny`, …), then the auth name pattern. Each guard records `auth_by`. Presets: [configuration](configuration.md#framework-presets).
- Laravel kernel middleware and Django `MIDDLEWARE` apply to every route and are not repeated.
- **Filters.** `--unguarded` keeps routes with no auth guard. `--missing NAME` keeps routes with no guard whose name contains NAME.
- `search` matches the same guard names.

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

## Heuristics

Both are labelled `heuristic` in text and in JSON (`confidence`). Neither adds edges.

- `roundtrip` pairs a write of a stored property through a lossy transform with a read that seeds UI state (path: write, lossy call, property, read, seed, each with `file:line`). MCP tool: `roundtrip(prop)`.
- `lint async-state` runs all four rules unless `--rules` lists a subset. MCP tool: `lint_async_state`.

## explore

`cg explore` answers a question in one call: matching symbols, then entry points, call paths, blast radius and source. The same answer is the MCP tool `explore` ([MCP server](mcp.md)).

Resolution, in order:

- A spec that matches 1 to `--max-symbols` nodes wins (a node id, `catalog.api.place_order`, or `POST /api/orders/`). A bare word that only names a test fixture or a module falls through to the word match.
- A token that is a source file (`catalog/api.py`) uses the symbols defined in that file.
- Otherwise the words are stemmed and matched. `module`, `file`, `field`, `column`, `constant` and test nodes (tests, and code in test files such as `conftest.py`) are left out. A word in a symbol's own name counts more than one found only in its module path; the highest scores are kept.

Sections:

- **entry points** — up to six per symbol, with kind, name and confidence (`exact`, `resolved`, `heuristic`).
- **call paths** — the path from the first entry point, then a path between resolved symbols. A hop is `-KIND[confidence @ file:line]->`.
- **blast radius** — callers, tables written and read, env keys, jobs and connections, each with a confidence label.
- **source** — numbered lines, grouped by file, inside the token budget. A cut-off body ends with a `snippet(...)` hint; symbols that did not fit are listed the same way.
- **next** — the fine-grained calls to run after this answer.

The default budget is 3000 tokens (about 4 characters each), clamped to 500..20000. Header, entry points, call paths, blast radius and next steps come first; when they alone would not fit, the lowest-ranked symbols are dropped and listed under **next** as `explore(...)` calls. Source fills what remains. The last line is `budget: used/limit tokens`. Nothing matched exits 1.

| flag | |
|---|---|
| `--budget N` | token budget (default 3000, clamped to 500..20000) |
| `--max-symbols N` | symbols to expand (default 4) |
| `--json` | print the result object |
| `--db DB` | graph database (required) |

## affected

`cg affected` maps a change to the symbols it touches, then to the tests and entry points that reach those symbols. A path argument is the whole file. `--base REF` (alias `--git-diff`) reads `git diff` against that revision and keeps only the touched lines. The innermost symbol wins: a changed method, not the class that contains it. A pure rename uses both the new path and the old one; a rename the index has not caught up with falls back to the old path. A deletion is resolved from the index. A changed test is listed itself (`changed`). A file with no symbols is reported as not in the index.

The same expansion is what a file or module spec does in `cg tests` (`app/util.py`, `app.util`): every symbol defined in that file. See [Broadcast channels and tests](channels-and-tests.md#tests).

`--quiet` prints the test files, one per line, so a runner can take them:

```bash
pytest $(cg affected --base main --db out/graph.db --quiet)
```

At most `--max-targets` symbols are walked (default 200, `0` for no cap). The text report says when it stopped early and JSON reports it under `truncated`. With no test found, `--quiet` prints nothing, and a bare `pytest` would then run the whole suite. No files, no `--base` and no `--stdin` exits 2. A bad revision, or `--base` outside a git checkout, exits 2. Finding no tests is still exit 0.

| flag | |
|---|---|
| `--base REF`, `--git-diff REF` | only the lines the diff touches |
| `--stdin` | one path per line on stdin |
| `--root DIR` | git root (default: the indexed root) |
| `--quiet` | test file paths only |
| `--max-depth N` | transitive tests within N application hops (`0`: any) |
| `--unit-only` | leave out UI / snapshot tests |
| `--max-targets N` | cap on symbols walked (`0`: none) |
| `--json` | the result object, plus `completeness` |
| `--min-confidence` | `heuristic` (default), `resolved`, `exact` |

## agents

`install` and `update` write one block between `<!-- BEGIN cg agent rules … -->` and `<!-- END cg agent rules -->`. Bytes outside it stay. `remove` deletes the block. `show` and `--dry-run` print the diff only. Otherwise cg prints the diff and asks `apply N change(s)? [y/N]`; `--yes` skips that. A second run replaces the block in place; when nothing would change it reports `no change (block up to date)` and writes nothing.

The first selected file in the table order (`AGENTS.md`, else `CLAUDE.md`, else the Cursor rule) gets the full block. The others get a one-line pointer inside the same markers. The block pins `pip install 'cg-code-graph>=MAJOR.MINOR'` (the published package). Re-running converts an old full copy into a pointer when that file is no longer primary.

| `--target` | file |
|---|---|
| `agents` | `AGENTS.md` |
| `claude` | `CLAUDE.md` |
| `cursor` | `.cursor/rules/cg.mdc` |

`--target` repeats. `--all` selects all three. With neither, cg acts on the files that already exist, and exits 2 when none exist unless `--mcp` was given. `--dir` is the project root (default `.`).

`--mcp` adds, or on `remove` deletes, one `cg` entry under `mcpServers`: `{"command": "cg-mcp", "args": ["--db", "out/graph.db"]}`. Default path `<dir>/.cursor/mcp.json` (`--mcp-file` overrides). The entry is edited in place; other keys stay. Invalid JSON is left untouched. [mcp](mcp.md)

## install

`cg install` registers the MCP server under the key `cg`. `cg uninstall` removes it (and a cg-owned legacy `code-graph` entry). Both preview a diff and ask `apply N change(s)? [y/N]` unless `--yes`. `--dry-run` prints the diff and writes nothing.

| flag | meaning |
|---|---|
| `--host H` | `cursor`, `claude`, `claude-desktop`, `vscode`, `windsurf`, `codex`, `gemini`, `zed`, or `all`. Repeatable. Default: hosts already detected for the scope |
| `--project` | project config (default) |
| `--global` | user config. Requires `--db` (a global entry serves one graph) |
| `--dir ROOT` | project root (default `.`) |
| `--db DB` | graph file. Project default: `<dir>/out/graph.db` |
| `--tools LIST` | allowlist stored as `--tools` ([Choosing tools](mcp.md#choosing-tools)) |
| `--portable` | `command` `cg-mcp` and `--db out/graph.db`, for a file committed to git |
| `--uninstall` | remove, on `cg install` |
| `--dry-run` | print the diff and write nothing |
| `--yes` | skip the prompt |

Exit 0 on success or when nothing changes. Exit 2 on invalid JSON or TOML (the file is left untouched), a refused host, `--global` without `--db`, or when no host is detected. Exit 1 when the prompt is declined.

`cg uninstall` takes the same flags. See [Install into a host](mcp.md#install-into-a-host).

## hooks

`install`, `uninstall` and `status` manage one block in `post-commit`, `post-checkout` and `post-merge`. Nothing is installed by default. The block sits between `# >>> cg hooks (managed by cg hooks; edit with cg, not here) >>>` and `# <<< cg hooks <<<`. cg finds that directory with `git rev-parse --git-path hooks`, so `core.hooksPath` and worktrees apply. Linked worktrees share one hooks directory, and it holds one block, so the last `install` decides which root and `--db` it refreshes. A `core.hooksPath` outside the repository (shared by other repositories) gets a note at install time. Not a git repository: exit 2.

A missing hook becomes `#!/bin/sh` plus the block, mode `0755`. An existing shell hook keeps every other byte; the block is inserted immediately after the shebang, so a later `exit 0` cannot skip it. A second `install` replaces that block in place. A hook with no shebang, or a non-shell shebang, is left unchanged and reported as `skipped (not a shell hook)`. `uninstall` removes only the block and restores the original bytes. A file that then contains only the `#!/bin/sh` cg wrote is deleted.

The block never calls `exit`. It backgrounds the refresh, discards its output, and ends with a succeeding command, so git is not blocked and a hook failure cannot fail the git command. `post-checkout` does nothing when `$1` equals `$2` (HEAD unchanged). `CODEGRAPH_NO_HOOKS=1` skips the block ([Configuration](configuration.md#environment-variables)). The command is `'<python>' -m codegraph.cli refresh '<root>' --db '<db>' --quiet` with the Python that ran `cg hooks install`, when that file still exists; otherwise `cg refresh …` when `cg` is on `PATH`. If neither exists, the block does nothing. Run `install` again after moving or recreating that environment.

`cg refresh ROOT --db DB` re-indexes when the checkout changed. Every run touches `<db>.refresh.pending`, then takes `<db>.refresh.lock` (non-blocking `flock`, or `msvcrt.locking` on Windows; the OS releases either if the process dies). If another refresh holds the lock, it prints `refresh already running; marked pending` and exits 0. The holder clears the flag before each pass and runs another pass while it is set again, at most three passes per run, so a burst of checkouts collapses into a few runs and a request that arrives mid-pass is not lost. When the database exists and ROOT is a git repo, cg hashes the path, mtime and size of every file from `git ls-files -c -o --exclude-standard`; if that matches `<db>.refresh.state` (written by the last successful refresh, together with the database's mtime), it prints `up to date` and exits 0. A rename, an added or deleted file, an edit, or a database rewritten by something else (`cg index`) re-indexes. Not a git repo, or a workspace app root outside ROOT: always re-index. Otherwise it indexes into `<db>.refresh.tmp` (root, name and recorded `--python-root` / `--include-generated` flags from the database, or the CLI arguments when the database is new), refuses a 0-node result without replacing the database, then moves the file into place with an atomic rename. A workspace `apps:` list is indexed on that temporary path as well; per-app databases derived from it are moved next to the real `--db`. A combined graph from `cg link` (no `apps:`) is refused and left unchanged. Git's own `GIT_DIR` / `GIT_INDEX_FILE` and similar variables, which git sets for hooks, are ignored, so ROOT is always the repository indexed. The last run is written to `<db>.refresh.log`. Exit 0 means refreshed, up to date, or another refresh holds the lock; a failure or refusal is nonzero, and the hooks ignore it.

| Flag | Meaning |
|---|---|
| `--dir` | project root for `hooks` (default `.`) |
| `--db` | graph file; required for `install`; stored absolute. Required for `refresh` |
| `--dry-run` | print the diff and write nothing |
| `--yes` | skip `apply N change(s)? [y/N]` |
| `--quiet` | `refresh` only: no stdout |
| `--name` | `refresh` only: project name when the database is new |

The database file and its sidecars (`<db>`, `<db>.*` and `<db>-*`, including `-wal` and `.refresh.*`) are left out of the fingerprint, so a database that lives inside ROOT and is not gitignored still reports `up to date` on the next run.

## watch

`cg watch ROOT --db DB` runs the same refresh as the git hooks, once at start and again when sources change. Install the optional backend with `pip install "cg-code-graph[watch]"`. Without it, cg polls the git fingerprint (`--poll` forces that path). Polling needs a git repository and exits 2 otherwise.

`.gitignore` applies because a refresh runs only when the `git ls-files` fingerprint changes. `watchfiles` also skips `vendor`, `build`, `dist`, `target`, `out`, `.next`, `.nuxt` and `.dart_tool`, and both backends ignore the database and its sidecars. Polling waits until two checks `--debounce` seconds apart see the same fingerprint (at most 30s) and then refreshes once. Before each refresh, cg waits while `git rev-parse --git-path index.lock` exists (a branch switch), so a checkout is one refresh. If files change while a refresh runs, one more refresh follows, then cg waits for the next change. The run shares `<db>.refresh.lock` with `cg hooks`. Ctrl-C prints `watch stopped` and exits 0.

| Flag | Meaning |
|---|---|
| `--db` | graph file (required) |
| `--name` | project name when the database is new |
| `--debounce` | seconds to let writes settle (default 1) |
| `--interval` | polling interval in seconds (default 2, minimum 0.2) |
| `--poll` | poll with git instead of `watchfiles` |
| `--quiet` | pass `--quiet` through to each refresh |

## clean

Cache root: `$CODEGRAPH_CACHE`, else `$CODEGRAPH_CACHE_DIR`, else `%LOCALAPPDATA%\codegraph`, else `$XDG_CACHE_HOME/codegraph`, else `~/.cache/codegraph`. [configuration](configuration.md#environment-variables)

`doctor` prints size per kind. Paths under the root: `extractors/<language>-<lock hash>/`, `scip/<name>-v<N>-<project key>-<key>.scip` (and `.lock`), `scip/ra-config-<project key>-<hash>.json`, `scip/swift-v<N>-<build key>.lock`, `swift-build/<build key>/`, `ts/<project key>-v<N>-<fingerprint>.json`, `dart/<project key>-v<N>-<fingerprint>.json`, `projects/<project key>`. `v<N>` is the cache version. The project key is the first 12 hex digits of the SHA-256 of the resolved root.

| Invocation | Removes |
|---|---|
| `clean ROOT` | entries of that project and of projects indexed below it, every kind except extractors |
| `clean --stale` | older cache versions and layouts (names with no cache version or project key), `.tmp` files older than `CODEGRAPH_INDEXER_TIMEOUT` + 10 min, SCIP locks with no cache entry |
| `clean --all` | the cache root, extractors kept |
| `clean --all --extractors` | the cache root, including extractor installs |
| `--db DB` | that graph plus `-wal` / `-shm` / `-journal` (alone, the cache is left as-is) |
| `--dry-run` | paths and sizes only; `--json` is the same list |

Nothing outside the cache root is deleted except an explicit `--db`. Symlinks are removed, not followed. A lock a running cg holds is skipped. A non-SQLite `--db` is refused. If the root resolves to `/`, a drive root, `$HOME`, or a directory above `$HOME`, `clean` exits 2 and deletes nothing.
