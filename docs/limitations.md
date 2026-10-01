# Known limitations

code-graph is beta (v0.3). This page maps out where the current analysis ends, so you know how far each answer reaches.
Issues and pull requests that extend it are welcome.

- **Types are flow-insensitive** (one type set per variable per function), and PHP generics are outside the current scope. When a receiver is unresolvable, a unique-method-name fallback is used (`heuristic`, with a stoplist).
  A Builder passed as a generic `Builder` param loses its model.
- **Model → table** uses `$table` or Str::plural-like convention.
- Next to index:
  - seeders as entry points;
  - `Artisan::command` closures;
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
- Go and Java come in through SCIP: detection plus indexer recipes exist, and the recipes are next in line for testing.
- TS/Vue/Nuxt:
  - auto-imports and global components resolve through `.nuxt` when it exists. A clean checkout without `.nuxt` gets
    generated stand-ins for the project's own composables, utils, stores and components (a warning suggests
    `npx nuxi prepare`); without `node_modules`, Vue / Nuxt built-ins (`ref`, `useFetch`, …) stay unresolved, and
    module-provided auto-imports and `imports.dirs` beyond `composables/`, `utils/` and `stores/` need `.nuxt`;
  - the source directory is `srcDir` from `nuxt.config`, else `app/` or `src/` when they hold Nuxt directories, else
    the repo root; layers (`extends`) are not merged;
  - library components (Nuxt UI etc.) are not nodes; `<component :is>`, `Teleport`/`Transition` are not resolved;
  - functions declared inside a `.vue` `<script setup>` collapse into the component node;
  - parameter-dependent URLs are expanded one call level only; numeric literal args stay `{param}`;
  - the axios detection is by type name (`AxiosInstance`/`AxiosStatic`); other HTTP wrappers need to be added;
  - bases not traced to `axios.create` fall back to a heuristic suffix match;
  - no navigation edges (`NuxtLink to`, `navigateTo`), no parent/child nested-page links;
  - base URLs from runtime config and env are folded into endpoint paths when their value is in the repo
    (`nuxt.config` `runtimeConfig` defaults, `.env`, `.env.example`, `NUXT_PUBLIC_*` overrides, `process.env.X || '…'`
    defaults in code). Values set only in CI or the deployment stay an unknown origin; `ref()` values and `baseURL`
    assigned inside interceptors / `onRequest` hooks are not tracked (`computed(() => …)` is). When the path with the
    configured base matches no route, `link` retries without it and labels the match `heuristic`;
  - a request whose whole URL is an opaque value (a wrapper's parameter without callers) names no endpoint
    (`http_url_unknown` in the index stats);
  - i18n keys are one global namespace (per-SFC `<i18n>` scopes are not separated);
  - no server/ or api/ handlers (Nitro routes would need a small addition);
  - request keys: builder functions are followed up to 4 levels; call-site argument keys one level (direct callers of
    the request issuer). Untyped forwarded parameters stay "unknown".
- TS server frameworks (NestJS, Next.js, Express / Fastify / Koa / Hono; details in [ts-frameworks.md](ts-frameworks.md#limitations)):
  - `link` compares method + path only: Nest DTO fields and Fastify schema fields (`body_fields` / `query_fields`) and client
    `body_keys` / `query_keys` are stored but not compared;
  - Nest DI tokens are global: per-module provider scoping, `exports` visibility and token collisions are not modelled;
    request-scoped providers are treated as singletons; project-specific decorators (custom job / event systems built on
    `SetMetadata`, route decorators wrapped by `applyDecorators`) are not entry points;
  - Express-style middleware order is known only within one file; dynamic `require(path)` and routers passed through
    containers are not followed; a router never mounted from an app keeps its local path (`unmounted`, heuristic);
  - Next.js: regex `middleware.ts` matchers link every route (heuristic); `pageExtensions`, i18n locales and
    `generateStaticParams` are not applied; MDX/MD-only pages are not modelled; parallel / intercepting routes are best
    effort (an intercepting route gets the URI of the page it intercepts); for pages-router data functions
    (`getServerSideProps` etc.) only the CALLS made inside them are recorded;
  - monorepo roots are not split automatically: index each app (`apps/api`, `apps/web`) separately and `link` them;
  - JavaScript without types resolves only what the checker can infer (CommonJS exports, object literals);
  - calls through generated API clients, or bases configured outside the repo, have an unknown origin, so they always
    match `heuristic`.
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
  - results are the deterministic facts themselves; summarising them is up to you or your agent.
- Plans:
  - completeness rules are fixed and named, not open-ended. Covered: table writers/readers, model-bound surfaces (Filament
    `$model`, `$fillable`, JsonResource, FormRequest key overlap), callers/entry points, clients, mirrors, identity
    columns, text mentions and declared precedents. Anything else needs a plan entry or a `precedents` regex;
  - `role: guard` bypass detection only sees graph reads (Eloquent property access the PHP plugin resolves);
  - a guard is checked by proxy: the guard method (or a direct callee) reads the declared column. Whether the branch
    really blocks the forbidden path is not evaluated;
  - verify mode leaves judging an `intent` (free text) to the reviewer. It reports changed/UNCHANGED source spans against the baseline,
    plus the planned edges;
  - external clients come only from the snapshot file (private repos are not indexed); `code-search` entries assume the
    shared URL contract;
  - findings come from a hand-refreshed snapshot of issue evidence lines, not a live GitHub query; ranges map to the
    innermost method/function span;
  - text mentions only scan `app/` and `resources/views/` PHP/Blade and use exact names (identity columns, relation names).
- Visual view: a few hundred nodes per view is comfortable; above 1,500 nodes the closest ones are kept (`truncated`);
  layouts are force-directed (fcose) or layered (breadthfirst; does not handle module boxes well); serving is local
  (127.0.0.1) and has no auth, so expose it only through an SSH tunnel or use `viz-export`.
- Python / Django:
  - stdlib `ast` only: no type checker, so calls through untyped parameters, `**kwargs`, decorators that change signatures,
    `getattr`/registries and metaclass magic fall back to a unique-name `heuristic` match or stay unresolved;
  - URL confs built in loops/functions (e.g. plugin registries that generate `path()` lists at import time) and views
    registered through third-party registries (NetBox `register_model_view`, Wagtail hooks/viewsets) are only partly resolved;
  - GraphQL (graphene, strawberry, ariadne) schemas are not modelled as routes;
  - ORM reads/writes are detected on `Model.objects...`, related managers and instance `.save()/.delete()` when the receiver
    type is known; raw SQL and `QuerySet` values passed through untyped helpers are not;
  - response shapes are derived from returned dict literals, helper functions and declared `response=` schemas; values
    built dynamically (`dict(**x)`, `serializer.data` of a dynamic serializer) are opaque, so no missing-key issue is raised;
  - DRF: `get_serializer_class()` overrides are followed only when they return a class name; nested routers (drf-nested-routers)
    and custom router subclasses are approximated;
  - `ModelSchema` FK fields render as the related pk; their type is reported as `fk` and compared leniently.
- Dart / Flutter:
  - parse-only analysis (no `pub get` of the target, no analyzer resolution): types come from declarations, constructor
    calls, generics on `Future`/`List`/`Map`, `fromJson` and provider lookups; `var x = someCall()` across files is inferred
    from the callee's declared return type only;
  - DI frameworks (get_it, injectable, Riverpod) are resolved only for the typed lookup forms (`getIt<T>()`, `read<T>()`, `of<T>()`);
  - URLs built from runtime values (config objects loaded at runtime, interceptors that rewrite paths) become `{param}` or
    an `unknown` origin; Dio without a traceable `baseUrl` is matched by path suffix and capped at `heuristic`;
  - GraphQL/gRPC clients and Firebase SDK calls are not modelled as HTTP calls;
  - generated code: `.g.dart` is read for JSON keys, `.freezed.dart`, `.mocks.dart`, `.config.dart` and `.gr.dart` are skipped;
  - bloc `state_flow` works on `on<E>` handlers, `emit(...)` (incl. `copyWith(status: X.y)`) and UI checks
    (`is`, `switch`, `==` on enum-like values); states derived in `buildWhen`/selectors are not interpreted;
  - the extractor is built on `package:analyzer` 10.2 with the latest language version; files that only parse with the
    `primary-constructors` experiment (Dart 3.13 `class A(final int x)`) are re-parsed with it, other experiments are not enabled;
  - expressions deeper than 7 levels are stored as source text; route trees are recovered from the per-call facts, other
    deeply nested literals (big `Map` bodies inside builders) may lose keys;
  - relative `Uri(path: ...)` calls whose host is added later by a custom client (`send()` override) are kept with an
    `unknown` origin; HTTP stacks that are not `package:http`, Dio, `dart:io HttpClient`, Retrofit or Chopper (e.g. a
    Rust/FFI bridge, enum-built URL tables whose values come from enum constructor arguments) are not captured;
  - auto_route v4 annotation configs (`@MaterialAutoRouter(routes: [...])`) are not read, and `@RoutePage` pages carry no path;
  - route tables generated from data (`demos.map((d) => GoRoute(path: d.route))`) and custom routers are not modelled
    as pages; navigation targets to them stay `unresolved` page nodes.
- Broadcast channels and tests (details in [channels-and-tests.md](channels-and-tests.md#limits)):
  - channel names that cannot be evaluated keep a `{?}` segment, fully dynamic names are skipped, and custom
    broadcasters are not modelled;
  - Livewire Echo listeners (`echo-private:…` keys in PHP components) and server-side pusher clients are not client
    subscriptions;
  - a channel's checks are the calls its callback makes; whether they really restrict access is for the reader;
  - transitive test paths are static: a browser test that stubs the API still reaches the backend through its page;
  - tests are found by naming conventions (PHPUnit / Pest under `tests/`, `*.test.*` / `*.spec.*`, `__tests__/`,
    `e2e/`, `cypress/`, `playwright/`); parameterised cases are one node per declaration.
- Payload check: one route per endpoint (ambiguous matches are skipped); server shapes are static, so framework-generated
  bodies (validation 422, auth 401, 500 pages) are not known; enum checks need `choices=` or a `Literal`/`Enum` annotation.

## Coverage and missing indexers

- **Coverage is per language and per file found on disk.** `cg index` counts source files by extension (vendored,
  `node_modules`, build and VCS directories are skipped) and records each language as `exact`, `heuristic`, `skipped`
  or `unsupported`; `cg coverage` and the MCP `coverage` tool print it. Languages without a plugin (Go and Java
  without a SCIP index, Kotlin, Swift, Ruby, C#, …) are listed with their file counts and are not in the graph.
- **A missing toolchain degrades, it does not fail the index.** Without `php` (or the PHP extractor's `composer
  install`), PHP files are `skipped`; without `node` (or `npm ci` in the TS extractor), TypeScript / Vue files are
  `skipped`; the other languages still index and the reason plus an install hint is recorded. Without rust-analyzer or
  scip-clang, Rust / C / C++ index in `heuristic` mode. A plugin that fails while indexing is recorded as `skipped`
  with its error.
- **An empty answer is only as complete as the coverage.** Replies that come back empty, or name an unknown symbol,
  end with a coverage line; for code in a language that is not covered or only heuristic, use normal search and file
  reading. Code generated at build or run time, and files outside the indexed root, are not counted at all.
- **Symlinks.** Dangling symlinks (for example ones that point outside the checkout) are skipped with a warning per
  file instead of stopping the language; the TypeScript stats list them as `skipped_dangling_symlinks`, and the
  TypeScript walker does not follow symlinked directories.

## Rust, C and C++

- **Exact mode needs external indexers:** rust-analyzer for Rust, and scip-clang plus a `compile_commands.json` for
  C/C++. Without them everything is `heuristic`. Measured against exact mode on six public projects, heuristic call
  edges are 68–90% precise and 30–83% complete; plain C does well, while generic Rust and template C++ miss about half.
- **One build configuration.** The C/C++ index reflects the compile database's defines and the files it lists.
  Headers that no translation unit includes, and code in inactive `#if` branches, get nodes but no exact references.
  For Rust, rust-analyzer enables all features, but `#[cfg(target_os = ...)]` items for other platforms are not
  analysed.
- **Macros.** Rust items generated by `macro_rules!` or proc-macros (derives, `#[async_trait]` bodies) don't get their
  own nodes. References inside item-level macro bodies are recovered only when the body parses as Rust. C/C++
  macro-expansion references are kept as `resolved`, `via_macro`. Large expansions (more than 8 symbols at one site,
  `CODEGRAPH_C_MAX_MACRO_REFS`) are dropped as noise, so a symbol used only through such a macro can look unused.
- **Function pointers and closures:** taking a function's address is a REFERENCES_FN edge from the enclosing
  function, but where the pointer is later called is not tracked (no dataflow). Callbacks registered at runtime show
  up as referenced, not as called.
- **Dispatch over-approximates:** a `dyn Trait`, generic or virtual call reaches every impl or override in the graph,
  not only the ones that can actually flow there.
- **References outside items** (`use` declarations, file-level code outside any function or type, attribute
  arguments other than serde/clap) are not attributed to a function and are dropped. For ripgrep that is about 5k
  occurrences, mostly `use` lines.
- **Synthetic nodes:** compiler-generated members (implicit constructors, `operator=`, defaulted functions) and
  definitions the syntax layer couldn't parse are added from SCIP as nodes with `attrs.from = "scip"`.
- **C/C++ detection** steps aside in repos that are Cargo, npm, Composer, Go, Python or Dart projects, unless a compile
  database exists or `CODEGRAPH_CFAMILY=1` is set. Vendored directories are skipped by name.
- **Generated sources** listed in the compile database but not on disk (unity builds, generated `.c`) are not
  indexed, so build before indexing if they matter.
- **Not modelled:** Cargo build-script outputs (`OUT_DIR` includes), `include!`, cross-crate graphs for crates outside
  the workspace (dependencies are external), C++20 modules, Objective-C, CUDA.

## Route guards and forwarded keys

- **Route guards** (`routes`, guard matches in `search`) are the facts each framework plugin records on a route:
  Laravel route and group middleware, Nest guards / interceptors / pipes (including `APP_GUARD` and `useGlobal*`),
  Express-style route, router and `app.use` middleware, Next.js `middleware.ts` matchers and handler wrappers,
  django-ninja `auth=`, Django view decorators, access mixins and DRF `permission_classes` / `authentication_classes`.
  Laravel kernel middleware groups and Django's `MIDDLEWARE` setting apply to every route and are not repeated per route.
- **Auth** is decided by the guard's name (`auth`, `login`, `jwt`, `token`, `permission`, `IsAuthenticated`, `ApiKey`,
  …); a guard with a project-specific name counts once it matches `--auth-pattern`. Checks inside the handler body
  (`if (!req.user)`, `request.user.is_authenticated`) are not guards.
- **Sent but not forwarded** keys are found when a call site passes an object literal to a request helper whose request
  keys are statically known (query or body keys of the HTTP call, one call level). Spreads, keys built at runtime and
  helpers that forward an opaque object are left out, so no gap is reported for them.
