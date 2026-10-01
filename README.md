# code-graph

A deterministic code graph for impact analysis across a Laravel (PHP) backend and a Nuxt/Vue (TypeScript) frontend.
It answers questions like *"what depends on this DB connection, and is it reached at runtime or only from a command?"*,
*"which pages end up writing this table?"*, or *"where is this value resolved, and does the client ever send it?"*
Every edge carries `file:line` evidence and a confidence level. No LLM is involved in building or querying the graph.

The indexer only reads files. It never boots the app and never connects to a database.

## Quick start (bundled sample apps)

```bash
scripts/reproduce.sh
```

This sets up `.venv`, installs the extractor deps, indexes the two sample apps, links them, runs a few queries, checks
the example plan, writes two static HTML views to `out/viz/` and runs the tests.
The sample apps are small and fictional:

- `examples/bookstore-api`: a Laravel API (stores, books, orders, a second `warehouse` DB connection, a sales report with
  a timezone fallback chain, an admin panel, a feature flag `features.new_inventory.enabled`).
- `examples/bookstore-web`: a Nuxt SPA that calls that API (report page, composables, a Pinia store). The `.nuxt/` type
  shims are committed so the sample works without `nuxi prepare`.
- `examples/bookstore.gates.json`: one gate scenario (`new_inventory`).
- `examples/plans/`: an example planned change (`preorders.yaml`) plus finding/client snapshots.

Manual steps:

```bash
python3 -m venv .venv && .venv/bin/pip install protobuf grpcio-tools pytest mcp pyyaml
(cd codegraph/plugins/php/extractor && composer install)
(cd codegraph/plugins/ts/extractor && npm ci)
C=".venv/bin/python -m codegraph.cli"
$C index examples/bookstore-api --db out/api.db --name bookstore-api --gates examples/bookstore.gates.json
$C index examples/bookstore-web --db out/web.db --name bookstore-web
$C link --backend out/api.db --frontend out/web.db --db out/combined.db \
        --backend-name bookstore-api --frontend-name bookstore-web --report out/api_matches
$C reaches connection:warehouse table:warehouse_stock --db out/combined.db
$C resolutions timezone --db out/combined.db
$C plan check preorders --plans-dir examples/plans --db out/combined.db
```

Requirements: php-cli ≥ 8.2 and composer (the PHP extractor uses nikic/php-parser ^5.4), Node ≥ 20 (the TS extractor's
`typescript` and `@vue/compiler-sfc` versions are pinned in `codegraph/plugins/ts/extractor/package-lock.json`), and
Python 3.11+ with `protobuf` (SCIP import), `pytest`, `mcp` (MCP server) and `pyyaml` (plan files).

## Commands

- `index ROOT --db DB [--name N] [--gates FILE] [--scip FILE]`: detect languages/frameworks and build the graph.
- `link --backend DB --frontend DB --db OUT`: merge a backend and a frontend graph and match client HTTP calls to routes.
- `reaches SPEC... [--gate auto/none/NAME]`: everything that depends on the targets, grouped by entry classification.
- `impact METHOD`: reverse walk from a method up to its entry points.
- `downstream SPEC`: forward dependencies (page → composables → HTTP → routes → services → tables).
- `path SRC DST`: one shortest evidence chain.
- `writers TABLE`, `siblings SYMBOL`, `node SPEC`, `stats`: writers of a table, similar code, node details, counts.
- `api-calls SPEC`: client endpoints with call sites, request keys and the matched route.
- `resolutions CONCEPT [--within S]`: where a value is resolved, its fallback chains, and whether the client sends it.
- `plan {list,load,validate,check,baseline} NAME [--verify]`: the planned-change layer.
- `serve`, `viz-export`, `viz-plan`: the visual view (local server or self-contained HTML).

Most query commands take `--json`, `--min-confidence resolved` (or `exact`) and `--max-depth`.

### Query targets (specs)

- `table.column` or `column:table.column`: a DB column. `table:orders`: a DB table.
- `connection:warehouse`, `connection:tenant_*`: DB connection(s) from `config/database.php`, plus dynamic ones
  registered via `Config::set('database.connections.…')`.
- `env:WAREHOUSE_DB_HOST`, `config:database.connections.warehouse`: env / config keys.
- `Class::method`, `Class`, short or FQN: code symbols (suffix match).
- `page:/reports/:id`: a Nuxt page by its route path. `app/pages/x.vue`, `app/composables/useX.ts`: a TS module or Vue
  SFC by file (repo-relative, suffix match). `useX`, `useX.fn`, `fn`: a TS composable, store or function.
- `http:GET /v1/{store}/…` and `route:GET /v1/{store}/…`: a client endpoint / a backend route (`*` glob).
- `request_key:timezone`, `setting:reports.timezone`: value facts (see below).

Several specs in one `reaches` call are unioned.

## Architecture

```
codegraph/
  core/model.py      node/edge kinds, confidence levels, entry-point kinds
  core/store.py      SQLite schema + writer
  core/plugin.py     Project, GraphBuilder, LanguagePlugin, FrameworkPlugin
  core/detect.py     language/framework detection from project files
  plugins/php/       PHP language plugin
     extractor/extract.php   nikic/php-parser AST -> JSON facts (one PHP process for the whole project)
     plugin.py               symbol tables, name resolution, type inference, call resolution, hook API
     gating.py               gate scenarios (feature-flag pruning)
  plugins/laravel/   Laravel framework plugin (sits on PHP): routes, migrations, models, connections,
                     config/env, commands, scheduler, jobs, events/listeners, bindings, middleware, Filament,
                     value facts (values.py)
  plugins/scip/      generic SCIP importer (any SCIP indexer -> same node ids)
  plugins/ts/        TypeScript/Vue language plugin
     extractor/extract.mjs   TS compiler API + @vue/compiler-sfc -> JSON facts (one Node process per project)
     plugin.py               facts -> nodes/edges, client URL normalisation, http endpoint nodes, facts cache
  plugins/nuxt/      Nuxt framework plugin (sits on TS): .nuxt tsconfig/auto-imports/components, page routes,
                     layouts, entry kinds, i18n keys
  plugins/stubs/     SCIP-indexer recipes for Go, Rust, C/C++, Python, Java (untested stubs)
  indexer.py         detect -> language plugins (+framework hooks) -> framework contribute -> store -> entry tagging
  link.py            cross-repo link: backend DB + frontend DB -> combined DB with MATCHES_ROUTE edges
  query.py           reaches / impact / writers / siblings / downstream / path / api_calls
  concepts.py        the `resolutions` concept query
  plans.py           planned-change layer (plan files, checks, verify, baseline)
  mcp_server.py      MCP stdio server over any graph DB (incl. combined)
  viz/               local read-only web view (graph.py, server.py, static/)
  cli.py
examples/            sample apps, gate scenario, example plan
tests/               fixtures + tests on the sample apps, MCP end-to-end client
```

### Plugin interface

```python
class LanguagePlugin(ABC):
    name: str
    def detect(self, project: Project) -> bool
    def index(self, project, builder: GraphBuilder, frameworks: list[FrameworkPlugin]) -> dict  # stats

class FrameworkPlugin(ABC):           # e.g. Laravel on PHP, Nuxt on TypeScript
    name: str; language: str
    def detect(self, project) -> bool
    def register_hooks(self, lang_ctx) -> None    # before resolution: type rules + fact handlers
    def contribute(self, project, builder, lang_ctx) -> dict   # after: framework nodes/edges
```

- `GraphBuilder.add_node(kind, key, name, fqn=, file=, line=, end_line=, module=, doc=, lang=, attrs=)` gives the stable
  id `kind:key`. `add_edge(src, dst, kind, file=, line=, confidence=)` records the edge with its evidence.
- The PHP context (`PhpProgram`) offers hooks so frameworks can add language-level knowledge without forking the resolver:
  - **type rules** map an expression to a type, e.g. `Model::query()` gives `builder:Model`, `$model->relation` gives the
    related model, `app(X::class)` gives `X`;
  - **fact handlers** turn resolved facts into framework edges, e.g. `->where('col')` on `builder:Model` produces
    READS_COLUMN on the model's table and `DB::connection('x')` produces USES_CONNECTION.
- **SCIP path:** for languages with a SCIP indexer (scip-clang, rust-analyzer, scip-go, scip-typescript, scip-python,
  scip-java), a `ScipIndexerPlugin(language, markers, command)` wraps the indexer and `plugins/scip/importer.py` maps
  SCIP symbols to the same `kind:FQN` ids, so framework plugins can attach to them. `index --scip FILE` merges an
  existing SCIP index into the native graph.
- **Detection** (`core/detect.py`) looks for marker files: composer.json/artisan (php, laravel; filament via composer
  require), package.json/tsconfig (js/ts), nuxt.config.* or a `nuxt` dependency (nuxt), vue, Cargo.toml, go.mod,
  CMakeLists.txt/compile_commands.json, pyproject/requirements, pom.xml/build.gradle.

## Schema (SQLite)

```sql
nodes(id PK, kind, name, fqn, file, line, end_line, module, doc, lang, entry_kind, attrs JSON)
edges(id, src, dst, kind, file, line, confidence, conf_rank, attrs JSON)   -- file:line = evidence
edge_kinds(kind PK, propagates, description)    -- propagates=1: src depends on dst (used by reaches)
node_entry(node_id, entry_kind, entry_count, sample_entry)   -- which entry kinds reach each node
meta(key, value)                                 -- corpus, commit, stats
```

**Node kinds:** class, interface, trait, method (incl. functions), property, external_class (vendor placeholder), route, command, schedule, job,
event, listener, observer, admin (Filament surface), table, column, connection, config, env, script (migrations/routes/config files).
TS/Vue (lang='ts'): module (TS file), page, component, layout, app (Vue SFCs), composable, store, function, class, type,
http (client endpoint `http:<METHOD> <path template>`), i18n.
**module** is derived from the path or namespace (e.g. `Http/Controllers/Admin`, `Services`, `Console/Commands`, `Domain/X`). **doc** holds the PHPDoc text.

**Edge kinds** (✓ = propagates in `reaches`/`impact`):
CALLS✓, IMPLEMENTED_BY✓ (interface method → impl), OVERRIDDEN_BY✓ (parent → override), BOUND_TO✓ (container binding),
ROUTES_TO✓, USES_MIDDLEWARE✓, HANDLED_BY✓ (command → handle), SCHEDULES✓, DISPATCHES✓, LISTENED_BY✓,
READS_COLUMN✓, WRITES_COLUMN✓, MENTIONS_COLUMN✓ (heuristic: a literal equal to a distinctive column name, e.g. in validation rules), READS_TABLE✓, WRITES_TABLE✓,
USES_CONNECTION✓, REGISTERS_CONNECTION✓, READS_CONFIG✓, WRITES_CONFIG✓, READS_ENV✓, REFERS_TO✓ (config value → connection), CONFIGURED_BY✓, CONFIG_CONTAINS✓,
TS: IMPORTS, RENDERS✓ (template component usage), USES_COMPOSABLE✓, USES_STORE✓, HTTP_CALLS✓ (→ http endpoint), MATCHES_ROUTE✓
(http endpoint → backend route, combined DB only), USES_LAYOUT, USES_I18N, REFERENCES_TYPE.
MAPS_TO_TABLE, HAS_RELATION, CONTAINS, EXTENDS, IMPLEMENTS, USES_TRAIT, INSTANTIATES, INJECTS, REFERENCES (`X::class`), OBSERVED_BY, BINDS, DEFINES.

**Confidence:**
- `exact`: syntactically certain, e.g. a static call, `new X`, `$this->m()` or a literal key.
- `resolved`: needed type or name resolution, e.g. typed properties/params, constructor-promoted deps, inferred variable types, return types, or model → table.
- `heuristic`: a unique-method-name fallback, or a column-name literal.

**Entry kinds:**
- Runtime: `http_route`, `scheduled`, `queue_job`, `listener`.
- Operator: `artisan_command`, `admin_panel` (Filament).
- `observer`.
- UI (TS/Nuxt): `ui_page` (Nuxt pages), `ui_global` (app.vue, layouts, plugins, `*.global.ts` middleware). `reaches` shows a UI group.

Tagging is a forward closure from each entry node over propagating edges. `reaches` classifies each dependent as:
- **runtime**, if any runtime entry reaches it;
- **operator-only**, if only commands or admin panels reach it (one-off import or provisioning);
- **none**, if no entry point reaches it.


## How `reaches` works
1. A recursive CTE walks propagating edges in reverse from the target(s), with an optional minimum confidence, and
   records the depth.
2. For each dependent method, a BFS rebuilds the shortest evidence path to the target (each hop has
   `KIND @file:line [confidence]`).
3. Results are grouped by entry classification and module, along with the entry points reached. With a gate scenario
   indexed, dependents that are only reached through gated code are listed in a separate GATED group (see below).

`impact <method>` is the same reverse walk from a method, stopping at entry points. `writers <table>` lists the
WRITES_TABLE/COLUMN edges. `siblings <symbol>` lists the same method in sibling classes (same parent, interface or
trait), other users of the same tables, columns, config keys and connections, and co-callers ranked by Jaccard
similarity of their callee sets.

## TypeScript / Vue / Nuxt plugin
`plugins/ts` (language) + `plugins/nuxt` (framework), behind the same `LanguagePlugin`/`FrameworkPlugin` interface as PHP/Laravel.

- **One TS program for the project.** The Nuxt plugin points the extractor at `.nuxt/tsconfig.app.json` (generated by
  `nuxi prepare`), so `paths` aliases (`~`, `@`, `#imports`) and Nuxt auto-imports (`.nuxt/types/imports.d.ts`: composables,
  utils, stores, Vue/Nuxt APIs) resolve through the real type checker. Global components come from `.nuxt/types/components.d.ts`.
- **Vue SFCs** are parsed with `@vue/compiler-sfc`. Each `.vue` becomes a virtual `X.vue.ts` in the program: `<script>` /
  `<script setup>` text stays at its original offsets (other bytes blanked, so line numbers are 1:1), and every template
  expression / `v-on` handler is appended as a stub function (v-for / slot scope variables become `any` params) with a line map back
  to the template. Component tags become RENDERS edges: `exact` when imported in the SFC, `resolved` via the Nuxt components map;
  library tags (Nuxt UI `U*`, `NuxtLink`, ...) are counted but not nodes.
- **Symbol resolution** uses the checker (aliases, re-exports, destructuring `const { fetchX } = useApi()` via the type of the
  pattern, shorthand properties, Pinia actions through `defineStore` types). Calls go to the declaring function node;
  references inside a `.vue` are attributed to the component/page node.
- **Nodes:** `module:<file>`, `page|component|layout|app:<file>.vue` (page `name` = route from file-based routing: `[id]`→`:id`,
  `index`, `(group)`, `[...slug]`), `composable` (top-level `use*` in `composables/`), `store` (`defineStore`, attrs.store_id),
  `function|class|type:<file>#<qualified name>` (functions nested in composables are `useX.fn`), all with JSDoc in `doc`.
  i18n: `i18n:<key>` with `defined_in` (locale files + `<i18n>` SFC blocks); `t()/$t()` literal keys give USES_I18N.
  `definePageMeta({ layout })` gives USES_LAYOUT (else the default layout).
- **HTTP calls** (`$fetch`, `useFetch`, `useLazyFetch`, `ofetch`, `fetch`, axios static/instance methods, detected by the
  callee's type `AxiosInstance`/`AxiosStatic`, so custom API clients wrapping axios are covered): the URL is folded to a
  template by a deterministic string evaluator (template literals, `+`, consts, `let` initialisers, string-literal-union types
  (expanded, e.g. `export.${format}` → csv/xlsx), const object maps, function returns, `encodeURIComponent`/`String`/`trim`,
  `runtimeConfig.X` → `{runtimeConfig.X}`); unresolvable parts become `{name}` placeholders. The instance's `baseURL` is traced to
  `axios.create({ baseURL })`. If the URL depends on a parameter of the enclosing function, it is expanded at each call site
  (one level, attrs.via_helper). Query/body keys (`params`, `query`, `body`, `data`; object literals, vars and later property
  assignments, keys set under an `if` flagged `conditional`) go into attrs, and the object keys passed to HTTP-issuing functions
  are recorded on the CALLS edge (`arg_keys`).
- **Endpoint nodes:** `http:<METHOD> <path>`: scheme/host and the API origin placeholder are stripped, the query string is dropped.
  Each HTTP_CALLS edge keeps `url`, `base`, `client`, `expr`, `origin` (`api` = base of an axios instance, `other`, `unknown`,
  `same-origin`), `query_keys`, `body_keys`.
- **Facts cache:** extractor output is cached in `~/.cache/codegraph/ts/` keyed by the extractor code + lockfile, the config, and
  (path, size, mtime) of every project file outside `node_modules` (incl. `.nuxt` and the lockfile). `CODEGRAPH_NO_CACHE=1` disables it.

## Cross-repo link (combined DB)
`codegraph.cli link --backend out/api.db --frontend out/web.db --db out/combined.db [--report out/api_matches]`
copies both graphs into one SQLite DB (ids don't collide: PHP and TS ids use different key shapes; `file` gets the repo prefix,
`attrs.repo` is set) and adds `MATCHES_ROUTE` edges `http:<METHOD> <path>` → `route:<METHOD> <uri>`. All queries then work across
both repos (`impact` on a controller method lists `ui_page` entry points; `downstream`/`path` from a page go through to tables).
Entry tagging (`node_entry`, `node_entry_live`) is recomputed over the union; gate predicates are kept.

Matching (deterministic, `codegraph/link.py`):
- method must match (`ANY`, `GET|HEAD` allowed); routes from `routes/api.php` are also tried with the `/api` prefix;
- segment by segment: literal = literal; client placeholder ↔ route `{param}`; client literal → route `{param}` (counts as
  `resolved`); route segment with embedded params (`export.{format}`) matches literals by pattern; a client segment with an
  embedded placeholder fitting a route literal is `heuristic`; at least one literal segment must agree;
- best candidate = fewest heuristic fits, then most literal agreements; a tie is reported as ambiguous (heuristic);
- confidence: `exact` (all segments literal/param-to-param, base traced to the API client), `resolved` (literal into a param),
  `heuristic` (embedded fits, ties, or the base was not traced: origin `unknown` → suffix match against route URIs);
- unmatched reasons: dynamic URL, other origin (not the API), same-origin relative URL, method mismatch, no route.
The report (`.md` + `.json`) lists every endpoint with its call sites, match, confidence and evidence (`routes/api.php:<line>`).

## Gate scenarios (deterministic feature-flag pruning)
A gates file names scenarios and the settings that are true in each, e.g. `examples/bookstore.gates.json`:
`new_inventory` = `features.new_inventory.enabled`.

How it works:
- The PHP extractor emits a control skeleton per function.
- `codegraph/plugins/php/gating.py` abstract-interprets it, seeded by setting reads through the scenario's
  `setting_accessors` (e.g. `getSetting(<key>)`) and by memoised method summaries, so wrappers such as
  `FeatureGate::usesNewInventory()` are understood too (see `tests/gating_fixture`).
- A branch is dead when its path condition is unsatisfiable *because of* a scenario fact.
- Edges made from dead code get `edges.gate=<scenario>` plus `attrs.guard`/`guard_expr`.

Schema additions: `edges.gate`, `node_entry_live(scenario, …)` (entry tagging that skips gated edges), `gate_predicates`.

Usage: index with `--gates FILE`, then query with `reaches … --gate auto` (or `none`, or a scenario name). Each item
reports `gate_status`: `live`, `gated_target` or `gated_entry`. In the sample, `Admin\InventoryController::index` only
reaches the `warehouse` connection inside the old-inventory branch, so it is listed as gated for `new_inventory`.

## MCP server (stdio)
`.venv/bin/python -m codegraph.mcp_server --db out/combined.db --gates examples/bookstore.gates.json --plans examples/plans`
(`--root` sets the project root for a single-repo DB). Requires the `mcp` SDK. Tools:
- `reaches`, `impact`, `siblings`, `writers`, `node`, `search`, `stats`;
- `downstream` (forward dependencies), `path` (one shortest evidence chain between two specs), `api_calls` (frontend
  endpoints with call sites, request keys and the matched route + controller; `unmatched` filter);
- `resolutions(concept, within?, client?, detail?)` (see "Concept query");
- `plan_list`, `plan_load`, `plan_validate`, `plan_check(name, verify?, max_items?, review?)`, `plan_baseline`;
- `index`: re-indexes into a temp file, then swaps the DB atomically. On a combined DB pass `repo` (a name used at link
  time): that repo's own DB is re-indexed from its recorded root and the link is rebuilt.

`tests/mcp_e2e.py` runs an SDK client end to end on the sample apps and writes `docs/mcp/sample_outputs.md`.

Example client config (e.g. Cursor `mcp.json`; replace `/path/to/code-graph`):
```json
{"mcpServers": {"code-graph": {
  "command": "/path/to/code-graph/.venv/bin/python",
  "args": ["-m", "codegraph.mcp_server", "--db", "out/combined.db",
           "--gates", "examples/bookstore.gates.json", "--plans", "examples/plans"],
  "cwd": "/path/to/code-graph"}}}
```

## Value facts
Deterministic facts about *values*. They come from the same PHP/TS ASTs and are emitted by `codegraph/plugins/laravel/values.py`
and the TS extractor:

| node | edges into it | from |
|---|---|---|
| `request_key:<k>` | READS_INPUT (`via`, `flow`, `default`) | `$request->input/query/get/boolean/…('k', default)`, `request('k')`, `$validated['k']` / `$filters['k']` where the array is request data (see flow) |
| | VALIDATES (`rule`) | FormRequest `rules()` keys (inline `$request->validate([...])` rules are not emitted as VALIDATES; their keys are read through `$validated['k']`) |
| `method:…::rules` | VALIDATED_BY | an action whose parameter is typed with that FormRequest |
| `setting:<key>` | READS_SETTING / WRITES_SETTING (`owner`, `default`) | `getSetting('key', default)` / `setSetting` on classes declaring them (e.g. `Store`) |
| `resolution:<fn>#<target>@<line>` | HAS_RESOLUTION (fn → resolution); FALLS_BACK_TO (resolution → each source, `order`, `default`) | a value picked through a fallback chain (see below) |

- **Request-array flow** (through more than one helper level): a fixpoint over call arguments. Sources are request
  accessors (`validated()`, `all()`, `input()`, `only()`, … on a receiver typed Request/FormRequest), `request()`, arrays
  built from request keys, and app functions that *return* request data.
  The result is a per-parameter "this array is request data" fact with an evidence trail, e.g. in the sample
  `SalesReportService::build` reads `$filters['timezone']`, `report()` passes `$filters`, and `ReportController::top`
  passes the validated request.
- **Resolution sites**: an assignment or return whose expression is a `??` / `?:` chain (ternaries and `match` are
  expanded too), or a function with ≥ 2 ordered early returns (a `resolveX()` helper). Each operand is expanded into
  atoms: request key, setting (+ its literal default), model attribute → column (the Store attribute
  `default_timezone` → `stores.default_timezone`), a builder `value('col')` call, config/env, literal.
  Transparent wrappers (`strtoupper`, `trim`, casts) are recorded as `norm`. Locals are inlined from their assignment;
  parameters are expanded through their call sites (depth ≤ 4). A site is kept when its chain has a source and ≥ 2
  entries; the flattened `signature` is stored on the node.
- **TS literal fallbacks**: the outermost `a ?? b || 'LIT'` chain ending in a string/number literal becomes a
  `resolution:` node owned by the enclosing function/component (in the sample: `rows.value[0]?.timezone ?? 'UTC'` in
  `pages/reports/[id].vue`). Client call sites record the keys they pass through variables and builder calls
  (`arg_keys`, conditional keys apart), and a request builder that forwards a typed parameter takes its keys from the
  type (`forwarded`, with the argument position).

## Concept query: `resolutions`
`codegraph.cli resolutions CONCEPT [--within SUBSTR] [--no-client] [--json]` (MCP tool `resolutions`). Deterministic:
1. **Sites**: resolution nodes whose target name (or first source key) has the concept as its head word
   (`$timezone`, `resolveTimezone`, `customerTimezone`, `reports.timezone` …; singular/plural).
2. **Chains**: sites grouped by signature into lettered chains, each with its ordered fallback steps and file:line,
   plus the routes that reach the site (reverse closure).
3. **Divergence**: pairwise between request-driven chains: common prefix, first differing step, final fallback.
4. **Client**: for every frontend endpoint matched to a route that reaches a request-driven site: the request
   builder's key status (always / conditional / absent / unknown), each call site's passed keys, client literal
   fallbacks of the issuer or its callers, and a verdict (`sent`, `sent by some call sites`, `never sent (…)`, `unknown`).

Result for `resolutions timezone` on the sample (abridged):
```
[A] input:timezone > column:orders.customer_timezone > setting:locale.timezone > column:stores.default_timezone > 'UTC'   (1 site)
    ReportController::resolveTimezone  return  (returns)  @bookstore-api/app/Http/Controllers/ReportController.php:43
[B] input:timezone > setting:reports.timezone > 'UTC'   (1 site)
    SalesReportService::build  $timezone  (expr)  @bookstore-api/app/Services/SalesReportService.php:17
  [A] vs [B]: same up to input:timezone; then [A] column:orders.customer_timezone vs [B] setting:reports.timezone; same final fallback 'UTC'
  GET /v1/main/admin/reports/top  -> chain B via GET /v1/{store}/admin/reports/top
     request app/composables/useReports.ts#useReports.fetchTop @bookstore-web/app/composables/useReports.ts:9  builder keys: timezone: conditional
       called by app/pages/reports/[id].vue @bookstore-web/app/pages/reports/[id].vue:10  sends timezone=no  (passes: category_id, date_from)
       client fallback @bookstore-web/app/pages/reports/[id].vue:12: rows.value[0]?.timezone ?? 'UTC'
       => 'timezone': never sent (builder key is conditional and no call site passes it)
```

## Visual view
`codegraph.cli serve --db out/combined.db [--port 8177] [--host 127.0.0.1] [--plans-dir examples/plans] [--presets FILE]`:
stdlib HTTP server, read-only, Cytoscape.js + fcose vendored under `codegraph/viz/static/vendor` (MIT, versions in
`VERSIONS.txt`; no CDN at runtime). Open `http://127.0.0.1:8177/`.
- Search box with suggestions; modes `reaches`, `impact`, `downstream` (sinks: tables via columns / routes / all),
  `path` (`source, [waypoints…,] target`) and `plan overlay`; min confidence; presets (built-in ones target the sample
  app; `--presets FILE` loads a JSON list of `{id, label, mode, specs[, sinks]}`). State is in the URL hash
  (`#mode=reaches&spec=…&expand=<group>|…&select=<node id>`), so views can be bookmarked.
- The subgraph is the union of the query's evidence paths. Nodes are grouped into boxes by module (`repo · module`;
  columns by table; HTTP calls, settings and request keys by kind). Big modules start folded into one box with counts;
  click a folded box to open it, double-click an open box to fold it. Edges between folded boxes are merged and
  labelled with the evidence-edge count.
- Colour = node kind; ◆ entry points with their kind (`[HTTP route]`, `[artisan]`, `[UI page]` …); ★ query targets;
  dashed red border = gated for the indexed gate scenario. Edges: solid = exact, dashed = resolved, dotted = heuristic,
  red dashed = gated.
- Click a node: docblock, file:line, source snippet (read from the indexed roots), entry kinds (and those still live
  under the gate), gate evidence, evidence edges in the view, and its first 25 in/out DB edges with attributes.
- `codegraph.cli viz-export MODE SPEC… --db … -o file.html [--sinks table,column]`: the same view as one
  self-contained HTML file (data + JS inlined) that opens from disk.
- Screenshots: `codegraph/viz/tools/shoot.mjs` (puppeteer-core with a locally installed Chrome; `SHOTS=file.json`
  for your own list). `scripts/reproduce.sh` writes sample screenshots to `out/screenshots/` when Chrome is available.

## Planned changes
A **plan** records an agreed scope as a small versioned YAML file, `<plans-dir>/<name>.yaml`. It is an overlay:
loading or checking it never writes to the graph DB. The checks compare it with the real graph and need no LLM.
Example: `examples/plans/preorders.yaml` (deliberately incomplete, so `plan check` has something to find).

Schema (`plan_version: 1`; `codegraph plan validate` reports unknown keys and wrong shapes with their path):
```yaml
plan_version: 1
name: preorders                      # slug, = file name
title: "..."
status: draft|agreed|in_progress|implemented|abandoned
issues: ["#7"]                       # linked findings (also per item)
context: {findings: findings.yaml, clients: clients.yaml}   # read-only snapshots next to the plan
rationale: |  free text
assumptions: ["only authenticated customers place orders"]
add_nodes:   [{id: column:books.preorder_until, attrs: {nullable: true}}]   # kind:key; column, table, method, route, setting, ...
modify:      [{target: StockService::reserve, intent: "...", role: guard, issues: ["#7"]}]
add_edges:   [{from: StockService::reserve, kind: READS_COLUMN, to: column:books.preorder_until}]
forbid:      [{id: no-warehouse-for-preorders, type: path, from: StockService::reserve, to: table:warehouse_stock,
               when: "...", guard: {at: StockService::reserve, reads: column:books.preorder_until}}]  # type: edge needs edge_kind
require:     [{route: "route:POST /v1/stock/reserve", middleware: ["auth:api"]}]
covers:      [{spec: ..., note: ...}]          # in scope, no change expected (silences a gap)
out_of_scope: [{spec: ..., reason: ...}]
precedents:  [{name: atomic counter, pattern: "increment\\(", within: [StockService::recordSale]}]
```

Specs are the same as everywhere else (exact node id, `Class::method`, `table:<t>`, `route:<METHOD> <uri>`, ...). In a plan, a
class spec means the class node, and `Class::method` must resolve to exactly one method; otherwise it is reported as ambiguous
with candidates. Request keys, settings, config and env keys named by a planned edge count as planned implicitly.

`codegraph plan check <name> --plans-dir examples/plans --db out/combined.db` (MCP `plan_check`) reports:
1. **Resolve**: every referenced node resolves. Planned nodes must not exist yet. Route middleware requirements are
   checked against the route's `middleware` attribute. Entry routes that reach the *same* modified method are compared, and
   any middleware a peer route has but this one lacks is listed.
2. **Missing from plan**: things the change touches that no plan entry covers, each with file:line:
   - writers of a changed table (payload writers are "missing"; others are "review");
   - readers of a changed table, tagged `bypasses <guard>` when they reach the table without passing the `role: guard` method;
   - Filament resources whose `$model` is the table's model (form saves are not WRITES edges);
   - `$fillable` lacking a new column;
   - JsonResources built by the table's code or named `<Model>Resource`;
   - FormRequests that validate most of the table's columns but not the new one;
   - direct callers and entry points of every modified method, with the call chain;
   - frontend pages calling affected routes (HTTP_CALLS/MATCHES_ROUTE), and external client call sites from the snapshot,
     matched with the same route matcher as the link step;
   - parallel tables/models (column-set overlap ≥ 50 %) and mirrored or same-class sibling
     methods reached from the modified code;
   - identity columns on other tables (e.g. `orders.book_id`) and their readers;
   - `text_mention`: exact-regex hits for those names in PHP/Blade files that have no column edge on that line (Blade views,
     JsonResource `$this->x`). These are labelled and never become edges;
   - **precedents**: plan-declared regexes searched in the modified methods' source.
3. **Conflicts**: forbidden paths or edges that still exist, with the chain. When the plan declares a guard, the report
   says whether it is present yet. It also lists open findings from the snapshot whose evidence lines fall inside nodes the
   plan touches (`linked in plan` / `NOT LINKED`).
4. With `--verify`: the **verify** section (see the workflow below).

The text report is compact: one line per item, `@ file:line`, and chains as `A -KIND@file:line-> B`. `--json` gives
everything; `-o` writes the report to a file.

**Workflow for agents**
1. Write `<plans-dir>/<name>.yaml`: the scope as agreed, issue links, forbidden paths, requirements.
2. Run `plan_validate`, then `plan_check`. Work through **MISSING FROM PLAN** and decide each item: add it to `modify`/`add_edges`,
   or to `covers` (handled, no change) or `out_of_scope` (with a reason). Link the **unlinked findings** or explain them.
3. Run `plan_baseline` (writes `<plans-dir>/<name>.baseline.json`: sha1 of each modified target's source span).
4. Implement.
5. Re-index (MCP `index`, or `codegraph index` + `link`).
6. Run `plan_check(verify=true)`. It shows:
   - planned nodes and edges that now exist in the real graph, with file:line. A planned READS_COLUMN also accepts
     MENTIONS_COLUMN or a same-class helper the method calls (`via reserveLocal`);
   - each modified target as `changed`/`UNCHANGED` against the baseline;
   - forbidden paths that are gone or now guarded (the guard method reads the declared column);
   - requirements that are met.

   `verify_ok` is true only when nodes, edges, forbids and requirements all pass. Any completeness gaps still open are
   listed next to it.

Overlay view: `serve` → mode **plan overlay** (preset "PLAN overlay: pre-orders", or `#mode=plan&spec=<name>`
[`&verify=1`]), or `codegraph viz-plan <name> --db … -o file.html` for a self-contained file.
- Planned new nodes: green, dashed. Planned edges: green, dashed.
- Modified targets: amber ring (hexagon = guard).
- Uncovered items: magenta halo plus a dotted magenta check relation to what they touch. Review items: dotted lilac.
- Covered items: green ring.
- Forbidden paths that still exist: red edges with a ⊣ mark; the forbidden target has a double red border.
- Filed issues are nodes with dashed lilac `touches` edges; a red border means the issue is not linked in the plan.
- Grey edges are real indexed evidence.

The panel starts with the full check report; clicking a node shows its role, every check that flagged it, and its evidence
and source. `&focus=<id>` zooms to a node's neighbourhood.

On the sample, `plan check preorders` resolves all references and reports 10 items missing from the plan (the Filament
form and the API resource for `Book`, `Book::$fillable`, `UpdateBookRequest`, the admin update writer, the
`POST /v1/orders` caller and entry route, the mobile client from the snapshot, ...), the forbidden path
`StockService::reserve → warehouse_stock` that still exists, the missing `auth:api` on `POST /v1/stock/reserve`
(its peer route `POST /v1/orders` has it) and an open finding that is not linked. `tests/bookstore_impl` is an
implementation of the plan as an overlay on the sample; `tests/test_plans.py` checks that verify mode reaches OK on it.

## Known limitations (beta)
- **Types are flow-insensitive** (one type set per variable per function) and there are no generics. When a receiver is unresolvable, a unique-method-name fallback is used (`heuristic`, with a stoplist).
  A Builder passed as a generic `Builder` param loses its model.
- **Model → table** uses `$table` or Str::plural-like convention.
- Not indexed yet:
  - seeders as entry points;
  - `Artisan::command` closures;
  - broadcast channels;
  - observers fired by model writes (observer nodes exist but aren't propagated from writes).
- Filament resources: all methods of a resource/page class count as admin entry points.
- Dynamic connection names are normalized (e.g. `tenant_{store.id}`). String-built column/table names are not resolved.
- Gate scenarios are deterministic but limited:
  - one scenario per index;
  - per-function context-insensitive (params are TOP, except literal-argument method summaries);
  - flags passed as data arguments or held in properties (`$this->x`) aren't tracked, so those paths stay live (conservative);
  - same canonical expression = same atom;
  - "inputs present" assumption for bare null/empty checks;
  - more than 12 atoms means the branch is assumed feasible;
  - middleware gates (a middleware that aborts when a flag is off) are not modelled;
  - an edge is gated only when the gated function itself makes the reference.
- Filament form saves are not emitted as WRITES edges (the plan checks list Filament resources separately).
- SCIP importer: references are attributed to the nearest enclosing definition by range, and scip-php closures show up as anonymous functions.
- Go, Rust, C/C++ and others are **stubs**: detection plus indexer recipes exist, but the indexers aren't installed or tested.
- TS/Vue/Nuxt:
  - needs `.nuxt` (run `nuxi prepare` after installing deps); without it auto-imports/global components don't resolve;
  - library components (Nuxt UI etc.) are not nodes; `<component :is>`, `Teleport`/`Transition` are not resolved;
  - functions declared inside a `.vue` `<script setup>` collapse into the component node;
  - parameter-dependent URLs are expanded one call level only; numeric literal args stay `{param}`;
  - the axios detection is by type name (`AxiosInstance`/`AxiosStatic`); other HTTP wrappers need to be added;
  - bases not traced to `axios.create` fall back to a heuristic suffix match;
  - no navigation edges (`NuxtLink to`, `navigateTo`), no parent/child nested-page links, no laravel-echo channel links;
  - i18n keys are one global namespace (per-SFC `<i18n>` scopes are not separated);
  - no server/ or api/ handlers (Nitro routes would need a small addition);
  - request keys: builder functions are followed up to 4 levels; call-site argument keys one level (direct callers of
    the request issuer). Untyped forwarded parameters stay "unknown".
- Value facts / `resolutions`:
  - concept matching is lexical (head word of the target / first source key), so `$code` holding a timezone is missed and
    `--within` is a substring filter;
  - the "returns" form assumes source order = fallback order (guards between returns are not evaluated);
  - locals use their last assignment (flow-insensitive); arrays built from request keys assume same-name keys;
  - helper inlining depth ≤ 4; some operands stay `expr:` (e.g. array elements of untyped payload arrays, or untyped
    model attribute reads);
  - a source repeated later in a chain is shown once (`($a ?? $fb) ?: $fb`);
  - TS fallbacks: only `??`/`||` chains that end in a string/number literal (no ternaries, no `''`/`0`);
  - response fields are not modelled (setting → API response → client state chains are not followed);
  - no AI summary step.
- Plans:
  - completeness rules are fixed and named, not open-ended. Covered: table writers/readers, model-bound surfaces (Filament
    `$model`, `$fillable`, JsonResource, FormRequest key overlap), callers/entry points, clients, mirrors, identity
    columns, text mentions and declared precedents. Anything else needs a plan entry or a `precedents` regex;
  - `role: guard` bypass detection only sees graph reads (Eloquent property access the PHP plugin resolves);
  - a guard is checked by proxy: the guard method (or a direct callee) reads the declared column. Whether the branch
    really blocks the forbidden path is not evaluated;
  - verify mode cannot judge an `intent` (free text). It reports changed/UNCHANGED source spans against the baseline,
    plus the planned edges;
  - external clients come only from the snapshot file (private repos are not indexed); `code-search` entries assume the
    shared URL contract;
  - findings come from a hand-refreshed snapshot of issue evidence lines, not a live GitHub query; ranges map to the
    innermost method/function span;
  - text mentions only scan `app/` and `resources/views/` PHP/Blade and use exact names (identity columns, relation names).
- Visual view: a few hundred nodes per view is comfortable; above 1,500 nodes the closest ones are kept (`truncated`);
  layouts are force-directed (fcose) or layered (breadthfirst; does not handle module boxes well); serving is local
  (127.0.0.1) and has no auth, so expose it only through an SSH tunnel or use `viz-export`.

## Tests
`.venv/bin/python -m pytest -q tests/` runs everything on the sample apps and small fixtures (gating shapes, value
facts, TS/Nuxt + link, plans, viz, MCP end to end). Generated outputs use `TZ=UTC`.
