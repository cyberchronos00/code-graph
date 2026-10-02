# Architecture

How code-graph is put together: the codemap, the plugin interface, how queries walk the graph, and the TypeScript/Nuxt and cross-repo pieces.

## Invariants

These hold everywhere in the code base; a change that breaks one is a design change, not a bug fix.

- **Deterministic edges.** Every edge comes from a parser, the type checker or a named rule, so the same code always
  gives the same graph.
- **Edges carry evidence.** The `file:line` of the code that produced them, plus a confidence level (`exact`, `resolved`, `heuristic`).
- **Static analysis.** The indexer works from source files alone, so it is safe on any checkout: the app, its code
  and its databases stay untouched.
- **Plans are overlays.** Loading or checking a plan leaves the graph DB unchanged.
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
     extractor/fw.mjs        framework-neutral facts for the TS server layers (decorators, router calls, instances, ...)
     plugin.py               facts -> nodes/edges, client URL normalisation, http endpoint nodes, facts cache
  plugins/nuxt/      Nuxt framework plugin (sits on TS): .nuxt tsconfig/auto-imports/components, page routes,
                     layouts, entry kinds, i18n keys
  plugins/native/    shared by Rust and C/C++: SCIP reader (scipread.py), tree-sitter helpers (ts.py), cached
                     indexer runner (runner.py), cfg/feature/#if gate evaluation (gates.py)
  plugins/rust/      Rust language plugin: cargo.py (workspace/packages/targets/features), syntax.py (tree-sitter
                     items, impls, cfg, unsafe, FFI, env, routes), plugin.py (rust-analyzer SCIP -> exact edges,
                     heuristic resolver fallback, trait dispatch, entry points)
  plugins/cfamily/   C/C++ language plugin: syntax.py (tree-sitter items, includes, #if regions, getenv, macro
                     masking), plugin.py (compile database, scip-clang -> exact edges, heuristic fallback,
                     virtual dispatch, entry points)
  plugins/python/    Python language plugin (stdlib `ast`, no Python env needed): modules/classes/functions, import
                     resolution (relative, `__init__` re-exports, aliases), type inference for calls, hook API
  plugins/django/    Django framework plugin (sits on Python): urls.py (path/re_path/include/namespaces), django-ninja
                     (NinjaAPI, Router, add_router, operations, auth, Schema/ModelSchema), DRF (routers, ViewSets,
                     @action, APIView, serializers), models -> tables/columns/relations, ORM reads/writes, settings +
                     env (os.environ/getenv/django-environ), signals, Celery, Channels, management commands, admin;
                     shapes.py builds response shapes from returned dict literals / schemas
  plugins/dart/      Dart language plugin
     extractor/bin/extract.dart  package:analyzer (parse only, no pub get) -> JSON facts; compiled once to .bin/
     program.py              libraries/parts/exports, import prefixes, type inference, call resolution
     http.py                 HTTP client calls (package:http, Dio + BaseOptions, dart:io HttpClient/WebSocket,
                             Retrofit/Chopper annotations), URL templates, body keys, response parsing
     models.py               json_serializable / freezed (.g.dart or annotations) and hand-written fromJson/toJson keys
  plugins/flutter/   Flutter framework plugin (sits on Dart): widgets/State, bloc/cubit events -> handlers -> emitted
                     states -> UI handling (`state_flow`), Navigator / go_router / auto_route pages, entry kinds
  plugins/tsweb/     helpers shared by the TS server layers: path templates, route nodes, env/config, ORM tables,
                     in-repo client -> route links
  plugins/nest/      NestJS framework plugin (sits on TS): modules, routes, DI, enhancers, jobs/events/messages, CLI
  plugins/nextjs/    Next.js framework plugin (sits on TS): app + pages router, route handlers, server actions, middleware
  plugins/express/   Express / Fastify / Koa / Hono plugin (sits on TS): routes, mounting chains, middleware
  plugins/stubs/     SCIP-indexer recipes for Go and Java (untested stubs)
  indexer.py         detect -> language plugins (+framework hooks) -> framework contribute -> completeness -> store -> entry tagging
  coverage.py        per-language parser mode + file completeness, unsupported source types, scoped completeness of answers
  blindspots.py      index-time detectors for route / handler registrations no plugin models (file:line samples)
  link.py            cross-repo link: backend DB + frontend DB -> combined DB with MATCHES_ROUTE edges
  payload.py         request/response field check for linked calls (client JSON keys vs server schema/shape)
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

- After `index()`, a language plugin may leave a per-file report in `self.file_report`: `{"seen": [...], "parse_failed":
  [...], "skipped_oversize": [...], "unmapped": [...], "excluded": [...]}` (repo-relative paths). `coverage.py` buckets
  every discovered file of the language from it ([completeness.md](completeness.md)); a discovered file missing from
  `seen` counts as excluded.
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


## Rust and C/C++ plugins

Both use the same two layers (details and env vars in [native.md](native.md)):

1. **Syntax (tree-sitter).** Every definition becomes a node with a stable key: the Rust path or the C++ qualified
   name, with a file prefix for file-local items. This layer also produces every fact a compiler index doesn't
   carry: cfg / `#if` regions, `unsafe`, FFI, env reads, routes, attributes, and entry kinds.
2. **References.** In exact mode, rust-analyzer or scip-clang writes a SCIP index (cached by source fingerprint).
   Each SCIP definition is matched to a syntax item by (file, line, name), and each reference occurrence is
   attributed to the innermost item whose range encloses it. The edge kind comes from the target's kind (CALLS,
   USES_TYPE, ACCESSES_FIELD, USES_VALUE, REFERENCES_FN). Without an index, a scope-aware name resolver
   (imports / `use`, the module tree, receiver type hints, unique-name fallback) produces the same edges, labelled
   `heuristic`. Node ids are identical in both modes.

Dispatch: trait and virtual method calls land on the declaring method. IMPLEMENTED_BY / OVERRIDDEN_BY edges then
fan out to every impl or override, so `reaches` on an impl method includes the callers that go through the trait.

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
  Without `.nuxt` (a clean checkout) it writes stand-ins to a temp directory: a `tsconfig.app.json` with the Nuxt aliases,
  `types/imports.d.ts` declaring the exports of `composables/`, `utils/` and `stores/` (plus Vue / Nuxt / Pinia built-ins
  when `node_modules` has them) and `types/components.d.ts` with Nuxt's path-prefixed component names, and warns that
  `npx nuxi prepare` gives the full picture. The source directory is `srcDir`, else `app/` or `src/` when they hold Nuxt
  directories, else the root.
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
  `runtimeConfig.X` / `useRuntimeConfig().public.X` / `$config.X` → `{runtimeConfig.X}`, `process.env.X` / `import.meta.env.X`
  → `{env.X}`, `computed(() => …).value`); unresolvable parts become `{name}` placeholders. The instance's `baseURL` is
  traced to `axios.create({ baseURL })`, `$fetch.create` / `ofetch.create` / `ky.create` (also through factory functions and
  Nuxt auto-imports). Config placeholders are then looked up (`plugins/ts/baseurl.py`): real `.env` files and
  `NUXT_PUBLIC_*` overrides, then the `runtimeConfig` default or a `||` / `??` default in code, then `.env.example`. A
  found value's path is prefixed to the endpoint (`attrs.base` records value and source); `link` retries without it when
  the prefixed path matches no route. If the URL depends on a parameter of the enclosing function, it is expanded at each call site
  (one level, attrs.via_helper). Query/body keys (`params`, `query`, `body`, `data`; object literals, vars and later property
  assignments, keys set under an `if` flagged `conditional`) go into attrs, and the object keys passed to HTTP-issuing functions
  are recorded on the CALLS edge (`arg_keys`).
- **Endpoint nodes:** `http:<METHOD> <path>`: scheme/host and the API origin placeholder are stripped, the query string is dropped.
  Each HTTP_CALLS edge keeps `url`, `base`, `client`, `expr`, `origin` (`api` = base of an axios instance, `other`, `unknown`,
  `same-origin`), `query_keys`, `body_keys`.
- **Realtime and tests:** Echo / pusher-js subscriptions become `channel_sub` nodes; test files (Vitest, Jest, Playwright,
  Cypress) are extracted with the rest and their edges rewritten to `TEST_*` kinds by `codegraph/tests_index.py`
  (`page.goto` / `cy.visit` → `TEST_VISITS`). See [channels-and-tests.md](channels-and-tests.md).
- **Discovery** skips dangling symlinks with a per-file warning and does not follow symlinked directories.
- **Facts cache:** extractor output is cached in `~/.cache/codegraph/ts/` keyed by the cache version, the extractor code + lockfile,
  the config, and (path, size, content hash) of every project file outside `node_modules` (incl. `.nuxt` and the lockfile), so
  any content change re-runs the extractor, even one that keeps the file size and mtime. `CODEGRAPH_NO_CACHE=1` disables it.

## TypeScript server and full-stack frameworks
`plugins/nest`, `plugins/nextjs` and `plugins/express` sit on the same TS program. `extractor/fw.mjs` emits generic facts
(decorators with described arguments, router-style calls, instances and their initialisers, exports, directives, env reads),
and the Python layers turn them into routes, DI edges and entry points. Details, path notation and validation numbers:
[ts-frameworks.md](ts-frameworks.md).

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
- catch-all route segments (`{rest*}` one or more, `{rest*?}` zero or more) absorb the remaining client segments;
  with origin `unknown`, suffix candidates are ranked by literal agreement before parameter fits;
- unmatched reasons: dynamic URL, other origin (not the API), same-origin relative URL, method mismatch, no route.
The report (`.md` + `.json`) lists every endpoint with its call sites, match, confidence and evidence (`routes/api.php:<line>`).

### Payload / field check
For every client endpoint with a single route match, `codegraph/payload.py` compares what the client sends and parses with
what the server declares or returns, and writes the result to the `payload_checks` table of the combined DB (plus a report section):
- client side: request body keys with types (map literals incl. collection-`if`/spread, `jsonEncode`, `toJson()` of a model,
  local map writes), and response parsing (keys read from the decoded JSON, `X.fromJson` models with the JSON path they
  are applied at, e.g. `data.items[]`, status-code checks);
- server side: ninja `Schema`/`ModelSchema` fields (type, nullability, required, aliases), DRF serializer fields, and
  response *shapes* derived from the returned dict literals / helpers / `Schema.from_orm` (per status code);
- issues: `trailing_slash` (ninja does not redirect, so high), `request_missing_required`, `request_unknown_field`,
  `request_case_mismatch`, `request_type`, `request_nullability` (an explicit `null` vs. an omitted key),
  `request_body_missing`, `response_missing_key`, `response_case_mismatch`, `response_type`, `response_nullability`,
  `response_enum_values`, `status_code`. Each carries `file:line` on both sides.
Error envelopes (`*Error*` models) are compared with every shape, including non-2xx; success models only with 2xx shapes.

## Python / Django plugin
Parsing uses the stdlib `ast` module (any Python 3 syntax the running interpreter understands); no project environment
or import of the project is needed. Call resolution: local/imported names (relative imports, `__init__` re-exports,
`import a.b as c`), `self`/`cls` methods with MRO, annotated params/returns, constructor results, `super()`, a few
container generics; otherwise a unique-method-name fallback labelled `heuristic`. `sync_to_async(f)(...)` and similar wrappers
count as calls to `f`. Django entry kinds: `http_route` (urls/ninja/DRF), `websocket` (Channels), `queue_job` (Celery
tasks), `listener` (signal receivers), `management_command` and `admin_panel` (operator).

## Dart / Flutter plugin
The extractor is a small Dart program using `package:analyzer` in parse-only mode (no `pub get` of the target project,
no resolution), compiled with `dart compile exe` (rebuilt when the extractor source or lockfile hash changes); its facts are
cached in `~/.cache/codegraph/dart`, keyed by the cache version, the config and the content hash of every `.dart` / `.yaml` / `.env*` file.
Resolution is done in Python (`program.py`) from imports (`package:` via every `pubspec.yaml` name, relative, `part`/`part of`,
`export show/hide`, prefixes). URLs are evaluated statically: string interpolation becomes `{param}`, constants/getters/
constructor-provided fields are followed, `String.fromEnvironment`/`dotenv` become `{env:NAME}` (values from `.env` files
are recorded), helper wrappers (`get(path: ...)`) are expanded at their call sites.

