# Architecture

How code-graph is put together: the codemap, the plugin interface, how queries walk the graph, and the TypeScript/Nuxt and cross-repo pieces.

## Invariants

These hold everywhere in the code base; a change that breaks one is a design change, not a bug fix.

- **Deterministic edges only.** Every edge comes from a parser, the type checker or a named rule. No model guesses.
- **Edges carry evidence.** The `file:line` of the code that produced them, plus a confidence level (`exact`, `resolved`, `heuristic`).
- **Static only.** The indexer reads files. It never boots the app, runs its code or connects to its databases.
- **Plans are overlays.** Loading or checking a plan never writes to the graph DB.
- **Read-only serving.** The MCP server and the visual view only read the graph (except the MCP `index` tool, which
  rebuilds it from source).

## Codemap

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


## How `reaches` works
1. A recursive CTE walks propagating edges in reverse from the target(s), with an optional minimum confidence, and
   records the depth.
2. For each dependent method, a BFS rebuilds the shortest evidence path to the target (each hop has
   `KIND @file:line [confidence]`).
3. Results are grouped by entry classification and module, along with the entry points reached. With a gate scenario
   indexed, dependents that are only reached through gated code are listed in a separate GATED group (see [configuration.md](configuration.md#gate-scenarios---gates)).

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
`codegraph.cli link --backend out/api.db --frontend out/web.db --db out/graph.db [--report out/api_matches]`
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

