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
- Routes registered from data (`Route::` inside `foreach`, `->each(...)`, `array_map(...)`) are not listed; each such
  loop is reported as a blind spot (detector `laravel_loop_routes`, see [completeness.md](completeness.md#blind-spots)).
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
    `SetMetadata`, route decorators wrapped by `applyDecorators`) are not entry points. Route decorators wrapped by
    `applyDecorators` or a decorator factory are reported per use as a blind spot (detector
    `nest_wrapped_route_decorator`, see [completeness.md](completeness.md#blind-spots));
  - Express-style middleware order is known only within one file; dynamic `require(path)` and routers passed through
    containers are not followed; a router never mounted from an app keeps its local path (`unmounted`, heuristic);
    routes registered in a loop or with a computed method (`router[r.method](r.path, ...)`) are reported as a blind
    spot (detector `express_loop_routes`);
  - Next.js: regex `middleware.ts` matchers link every route (heuristic); `pageExtensions`, i18n locales and
    `generateStaticParams` are not applied; MDX/MD-only pages are not modelled; parallel / intercepting routes are best
    effort (an intercepting route gets the URI of the page it intercepts); for pages-router data functions
    (`getServerSideProps` etc.) only the CALLS made inside them are recorded;
  - monorepo roots are not split automatically: list the apps in `.cg.yaml` `apps`
    ([configuration.md](configuration.md#monorepo-apps)), or index each app separately and `link` them;
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
  layouts are layered left to right (impact, downstream, path; depth or longest-path layers with barycenter ordering,
  not a full Sugiyama crossing minimisation) or force-directed (fcose: reaches, plan); serving is local
  (127.0.0.1) and has no auth, so expose it only through an SSH tunnel or use `viz-export`.
- Python / Django:
  - source roots are detected from the layout and packaging config, or set in `.cg.yaml`
    ([python.md](python.md)); `sys.path` changes made at run time (`sys.path.insert(...)` in a launcher) are not
    evaluated, so a launcher's directory outside `src/`, `lib/`, `python/` or a package parent needs
    `python.source_roots`. Files whose path is not an importable name (`my-scripts/`, `alembic/versions/1a2b_x.py`)
    stay `unmapped`. When two package trees claim one module name (`tests` in every project of a monorepo), the
    renamed tree's absolute imports of that name resolve to the tree that kept it;
  - stdlib `ast` only: no type checker, so calls through untyped parameters, `**kwargs`, decorators that change signatures,
    `getattr` and metaclass magic fall back to a unique-name `heuristic` match or stay unresolved. Dispatch tables,
    plugin lists, callbacks and registering decorators become function references and calls through the collection
    ([python.md](python.md#entry-points-and-function-references)); a table filled in a loop or by another function
    (`for name in NAMES: TABLE[name] = make(name)`) and calls through `getattr(obj, name)` are not followed.
    Functions registered through a decorator (`@registry.register`) or stored in a registry (`registry[key] = fn`)
    whose caller cg does not see are reported as blind spots (detectors `python_decorator_registration`,
    `python_registry_assignment`);
  - entry points: `__main__` blocks, `pkg/__main__.py`, packaging entry points and MCP / click / typer / Flask CLI
    registrations are modelled; module-level code that runs on import (`app = create_app()` in `wsgi.py`) is not an
    entry point, and console scripts declared only in a `setup.py` built at run time (entry points computed by code)
    are not read;
  - FastAPI / Starlette and Flask routes are modelled from the app / router / blueprint objects a module or function
    assigns, returns, or receives as a parameter. A parameter counts as an app when its annotation says so, or when
    it is used for route registration and the module imports one framework; one received from code cg cannot see
    (no call with a known app, no pytest fixture of that name) is a root app of its own. A view defined inside a
    function routes to that function (nested defs collapse into the enclosing def). An `APIRouter` / `Blueprint`
    that no app includes gets route nodes that are not entry points (`mounted: false`). Route nodes are keyed by
    method and path, so a Starlette `Host` / Flask `subdomain` route shares its node with a same-path route on
    another host (the `host` / `subdomain` attribute is the first one seen). Not read: Flask-Classful `FlaskView`,
    `url_value_preprocessor`-based URL parts, custom converters (`<path:x>` matches one segment), routes added in
    loops. A `Depends()` dependency's checks are read from its own source (statuses it raises, security schemes,
    nested dependencies up to 4 levels); checks made in middleware or in called helpers are not;
  - web frameworks without a plugin (Sanic, Litestar, Bottle, …): their route decorators are reported as blind spots
    (detector `python_decorator_routes`) instead of routes;
  - URL confs built in loops/functions (e.g. plugin registries that generate `path()` lists at import time) and views
    registered through third-party registries (NetBox `register_model_view`, Wagtail hooks/viewsets) are only partly
    resolved; the `urlpatterns` entries built that way and `include()` targets cg could not follow are reported as
    blind spots (detectors `django_dynamic_urlpatterns`, `django_unresolved_include`);
  - GraphQL (graphene, strawberry, ariadne) root fields are protocol endpoints (`endpoint:graphql:Query.x`,
    [protocols.md](protocols.md#graphql-root-fields)), not routes; object-type fields are not modelled;
  - Celery / RQ / Dramatiq jobs pair by task name and queue ([protocols.md](protocols.md#job-queues));
    tasks enqueued through a project wrapper (netbox `JobRunner.enqueue`), actors held in attributes and queue names
    computed at run time are not followed; worker processes are read from Procfile / compose / systemd / supervisor
    / scripts only (a worker started by a custom CLI, such as authentik's `ak worker`, is not seen);
  - Kafka / AMQP / Redis pub/sub and streams / MQTT / NATS producers and consumers pair by topic, routing key,
    channel or subject in JS / TS, Python, PHP, Kotlin and Rust ([protocols.md](protocols.md#message-brokers));
    Dart / Swift / C++ / Go clients, cloud queues (SQS / SNS / Pub/Sub), STOMP, ZeroMQ, JetStream consumers, names
    from config files and calls through a project's own wrapper class are not followed;
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
  - GraphQL clients (graphql_flutter, ferry), gRPC clients and Firebase SDK calls are not modelled as HTTP calls;
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
    `e2e/`, `cypress/`, `playwright/`, pytest's `python_files` / `testpaths`, `conftest.py`, Python `tests/` and
    `test/` directories); parameterised cases are one node per declaration;
  - Python HTTP test requests link to Django / DRF / django-ninja / FastAPI / Starlette / Flask routes; a URL a
    helper builds from its arguments stays unknown. Python tests that start the project's programs in a subprocess
    link to the entry point when the argument list is evaluable (also built by `append` / `extend` / `+=`, through
    the installed runners cg knows, or copied from a project file or template first). Command lists read from
    configuration at run time, scripts a test generates rather than copies, and copies made in another function
    (a fixture) are not followed. Rust, Node, PHP artisan and Dart process starts link when the program and script
    are literals in the call or assigned earlier in the same function; Go has no language plugin
    (`exec.Command(os.Args[0])`, `go run ./cmd/x` are not linked), and a Node script with only top-level code has
    no module node to link to. Tests and fixtures generated by pytest hooks at run time, and fixtures of installed pytest plugins, are
    not graph nodes.
- Payload check: one route per endpoint (ambiguous matches are skipped); server shapes are static, so framework-generated
  bodies (validation 422, auth 401, 500 pages) are not known; enum checks need `choices=` or a `Literal`/`Enum` annotation.

## Coverage and missing indexers

- **Coverage is per language and per file found on disk.** `cg index` counts source files by extension (vendored,
  `node_modules`, build and VCS directories are skipped) and records each language's parser mode (`exact`,
  `heuristic`, `skipped`, `unsupported`) and its file completeness (discovered, indexed, parse failed, over the size
  limit, unmapped, excluded); `cg coverage` and the MCP `coverage` tool print it. Source types without a plugin (Go and
  Java without a SCIP index, QML, shell scripts, …, also extensionless scripts by their `#!` line) are
  listed with their file counts and are not in the graph. Per-file reports come from the Python, PHP, Dart, Rust and
  C/C++ plugins; TypeScript / JavaScript report file counts. Details: [completeness.md](completeness.md).
- **A missing toolchain degrades, it does not fail the index.** Without `php` (or the PHP extractor's `composer
  install`), PHP files are `skipped`; without `node` (or `npm ci` in the TS extractor), TypeScript / Vue files are
  `skipped`; the other languages still index and the reason plus an install hint is recorded. Without rust-analyzer or
  scip-clang, Rust / C / C++ index in `heuristic` mode. A plugin that fails while indexing is recorded as `skipped`
  with its error.
- **Answers say when they are partial.** Route lists, caller lists, `reaches`, `tests` and plan checks add a
  `coverage note:` when a blind spot or a file that is not indexed could affect them (scoped to the languages and
  directories of the answer), and MCP replies carry a `completeness` object. Replies that come back empty, or name an
  unknown symbol, end with a coverage line. Blind-spot detection covers the patterns listed in
  [completeness.md](completeness.md#blind-spots); code generated at build or run time, and files outside the indexed
  root, are not counted at all.
- **Generated and copied files** are recognised by `.gitattributes`, generator banners in the leading comment block,
  framework build paths, generator file names, Capacitor / Cordova copy targets and `.openapi-generator/FILES`
  ([generated.md](generated.md)). Generated code without any of these (a hand-rolled generator with no banner) is
  indexed as source until `generated.paths` in `.cg.yaml` names it. A Capacitor `webDir` counts as build output when
  its `.gitignore` lists it or `angular.json` builds into it; a hand-written `webDir` stays source. `dist/` and
  `build/` are listed as build output directories without counting their files. Copies map back to their source per
  file; source maps of minified bundles are not read.
  Web / native bridge calls are linked for Capacitor plugins, React Native / Expo modules, Flutter method / event
  channels and Pigeon APIs, including Flutter `invokeMethod` and Pigeon `@FlutterApi` calls from native into Dart
  ([bridges.md](bridges.md)); React Native events, Capacitor `notifyListeners`, Cordova plugins and native UI
  components are not, and Java / Objective-C receivers are stubs without a call graph inside them. Electron IPC /
  context bridge and Tauri commands are linked across processes; Electron `MessagePort` / `utilityProcess`, Tauri
  events and commands invoked from `.svelte` files are not.
- **Symlinks.** Dangling symlinks (for example ones that point outside the checkout) are skipped with a warning per
  file instead of stopping the language; the TypeScript stats list them as `skipped_dangling_symlinks`, and the
  TypeScript walker does not follow symlinked directories.

## Rust, C and C++

- **Exact mode needs external indexers:** rust-analyzer for Rust, and scip-clang plus a `compile_commands.json` for
  C/C++. Without them everything is `heuristic`. Measured against exact mode on six public projects, heuristic call
  edges are 68–90% precise and 30–83% complete; plain C does well, while generic Rust and template C++ miss about half.
- **One build configuration.** The C/C++ index reflects the compile database's defines and the files it lists.
  Headers that no translation unit includes, and code in inactive `#if` branches, get nodes but no exact references.
  For Rust, rust-analyzer enables all features and analyses the host's `cfg`; calls into items gated for other
  targets come from the syntax layer (`via: cfg-inactive`), and every item carries the targets it is built for
  ([platforms.md](platforms.md)).
- **Macros.** Rust items generated by `macro_rules!` or proc-macros (derives, `#[async_trait]` bodies) don't get their
  own nodes, except tests from a project macro that expands to `#[test] fn $name` (only the name, the invocation's
  calls and the body's path calls are kept; method calls in the macro body are not). References inside item-level macro bodies are recovered only when the body parses as Rust. C/C++
  macro-expansion references are kept as `resolved`, `via_macro`. Large expansions (more than 8 symbols at one site,
  `CODEGRAPH_C_MAX_MACRO_REFS`) are dropped as noise, so a symbol used only through such a macro can look unused.
- **Function pointers and closures:** taking a function's address is a REFERENCES_FN edge from the enclosing
  function, but where the pointer is later called is not tracked (no dataflow). Callbacks registered at runtime show
  up as referenced, not as called.
- **Dispatch over-approximates:** a `dyn Trait`, generic or virtual call reaches every impl or override in the graph,
  not only the ones that can actually flow there. The same holds for a call on a TypeScript interface-typed value:
  it reaches every class implementing the interface. Implementations count when a class `implements` the interface,
  `new X()` or a class value is used where the interface (or its constructor) is expected, an object literal is
  typed as the interface, or a mixin class expression is applied to a class merged with the interface. An
  inherited `Sub.method` spec, and an override `Sub.method` (the calls into the base method it overrides), are
  narrowed only where the receiver type is known: TypeScript checker types, Python inferred instances (an
  annotation, `x = B()`, `self.x = B()`, an unannotated factory whose returns are all one class). Generic
  substitution (`Repo[B]().get()` returning `T`) is not inferred.
- **Callbacks passed as props:** a function-typed interface property (`onPress: () => void` in a React `Props`)
  is a member node only when a project class or object literal implements the interface. A callback passed as a JSX
  attribute (`<Button onPress={save} />`) is not an implementation: `onPress()` inside `Button` has no target, and
  `save` is reached from the JSX site (`CALLS` with `ref: true`), not from `Button`. Linking each JSX site's callback to the property
  would make every prop of a shared component a hub over all its call sites, so this is left out on purpose (#96).
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

## Kotlin

- **Exact mode is opt-in and version-bound:** the scip-java run executes the Gradle / Maven build, so cg only starts
  it with `CODEGRAPH_KOTLIN_SCIP=1` (or takes a prebuilt index via `--scip` / `CODEGRAPH_KOTLIN_SCIP_FILE`). scip-java
  0.12.3's semanticdb-kotlinc plugin loads into Kotlin ≤ 2.1 compilers only; Kotlin 2.2+ builds, and Android modules
  without an SDK, fall back to heuristic mode with the reason in `cg coverage`. Only call / constructor edges come
  from the index; declarations, framework facts and type relations stay those of the syntax layer.
- **Heuristic facts by name:** Spring Data reads / writes are classified by method name and need a receiver typed as
  the repository; Exposed access needs the table object as the direct receiver; `SecurityFilterChain` rules are read
  from string patterns in source order (beans with custom `RequestMatcher`s or several chains with `securityMatcher`
  are not separated). Navigation routes built from constants (`composable(Routes.TASKS)`) give no page.

## Swift

- **Exact mode is opt-in and limited to what compiles:** `swift build` runs the package manifest and plugins, so cg
  only starts it with `CODEGRAPH_SWIFT_INDEX=1` (or reads an existing store via `CODEGRAPH_SWIFT_INDEX_STORE`). A Linux
  build indexes only the targets that compile there (no SwiftUI / UIKit, no iOS-only apps); Xcode projects without
  `Package.swift` need a store from Xcode. Files outside the store, and code in inactive `#if` branches, keep heuristic
  edges. Only call / constructor edges come from the index; declarations and framework facts stay those of the syntax
  layer.
- **Heuristic facts by pattern:** Moya paths and methods are read from string literals and `.get` / `.post` values in
  the `TargetType` (`MultiTarget`, paths built in helpers and per-environment base URLs are not followed); Fluent
  writes need the model variable typed by a parameter, property or a `let` in the same function, and relations or raw
  SQL are not tables. OS versions (`@available(iOS 17, *)`, `#available`) are recorded as minimum versions
  (`attrs.available`), not platform conditions. URLComponents, typed endpoint enums other than Moya and `Info.plist` / `.xcconfig` base URLs are
  not read yet.

## Platform-specific code

- **Conditions are read from the source text and evaluated per target.** Conditions on feature flags, build
  macros (`HAVE_X`), API levels (`Platform.Version`) or architecture alone count as unknown and keep their code in
  every target's view; `cg platforms` lists them. Flutter's `defaultTargetPlatform` on the web is the browser's OS,
  so `TargetPlatform` checks are unknown for `web`.
- **Platforms from runtime values** (a platform string passed through a variable, a `Platform` wrapper class) are not
  followed; the condition must name `Platform.OS`, `Platform.isX`, `process.platform`, `cfg!` or a platform macro.
- **Variant grouping** links platform files (`x.ios.ts` / `x.android.ts` / `x.ts`), conditional-import alternatives,
  Rust / C definitions repeated per `cfg` / `#if` branch and items of per-platform sibling modules. Variants behind a
  runtime factory or dependency injection are separate symbols.
- **Per-target indexes:** one graph holds every target; `--platform` filters it by the conditions. Rust exact mode
  runs rust-analyzer once more per target the `cfg` conditions name (up to 3), so references under another target's
  `cfg` are exact; C / C++ (one compile database per platform), Swift and Kotlin builds per target are not merged
  yet. Swift `@available` / `#available` versions are recorded (`attrs.available`, minus what the deployment target already meets), not used as filters. Swift
  `#if os(...)` / `canImport` / `targetEnvironment` blocks come from the Swift plugin ([swift.md](swift.md)); Kotlin
  Multiplatform source sets and `expect` / `actual` from the Kotlin plugin ([kotlin.md](kotlin.md)); Electron / Tauri
  process boundaries from the bridges pass ([bridges.md](bridges.md#desktop-process-boundaries-electron-and-tauri)).
- **C heuristic mode:** functions generated by a project macro are found when the macro body defines the function
  (`int get_##name(...) {`) and the expansion stands at file level; macros that expand to another macro, or whose
  name is built outside the macro, are not. A definition tree-sitter cannot parse even on its own keeps a node from
  its head (`recovered: head`, calls by name only). Exact mode with a compile database has every definition.
- **Re-exports in variant files:** TS `export {a as b} from`, `export {x}`, `export *`, `export const X = Y` and Dart
  `export ... show` / top-level tear-offs count as definitions of the variant; a component wrapped in a call
  (`export const List = memo(Impl)`) does not.

## Route guards and forwarded keys

- **Route guards** (`routes`, guard matches in `search`) are the facts each framework plugin records on a route:
  Laravel route and group middleware, Nest guards / interceptors / pipes (including `APP_GUARD` and `useGlobal*`),
  Express-style route, router and `app.use` middleware, Next.js `middleware.ts` matchers and handler wrappers,
  django-ninja `auth=`, Django view decorators, access mixins and DRF `permission_classes` / `authentication_classes`.
  Project-wide defaults (Laravel kernel middleware groups, Django's `MIDDLEWARE` setting, DRF
  `DEFAULT_PERMISSION_CLASSES`) and middleware a Laravel controller registers in its constructor
  (`$this->middleware(...)`) apply outside the route definition and are not repeated per route, so routes they protect
  are listed by `--unguarded`; [validation.md](validation.md#presets-starter-queries-and-route-guards) shows how
  often that happens on public projects.
- **Auth** is decided by the framework preset's guard lists first ([configuration.md](configuration.md#framework-presets)),
  then by the guard's name (`auth`, `login`, `jwt`, `token`, `permission`, `ApiKey`, …); a guard with a
  project-specific name counts once it matches `auth.extra_patterns` in `.cg.yaml` or `--auth-pattern`. Checks inside
  the handler body (`if (!req.user)`, `request.user.is_authenticated`) are not guards.
- **Presets** cover the frameworks with route plugins; Rust (axum / actix) and C / C++ get the language skip lists
  and the shared name pattern. `.cg.yaml` `exclude` and `skip_dirs` apply to every plugin; the TypeScript and Dart
  extractors' built-in skips (build output, generated files) stay on. Starter queries are recorded per indexed repo;
  a combined graph computes them when asked.
- **Sent but not forwarded** keys are found when a call site passes an object literal to a request helper whose request
  keys are statically known (query or body keys of the HTTP call, one call level). Spreads, keys built at runtime and
  helpers that forward an opaque object are left out, so no gap is reported for them.
