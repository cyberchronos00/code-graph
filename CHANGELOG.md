# Changelog

All notable changes to code-graph are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html). While the version is 0.x, minor releases may change
commands, output and the graph schema; such changes are listed under **Changed**.

## [Unreleased]

### Fixed

- `impact` keeps the override relation apart from the callers: a base method is listed as `overrides:` instead of
  as a caller of its override, and `impact` on an abstract or base method lists the code that calls its overrides
  (`via override`). The MCP structured content carries the relation as `overrides`
  ([#26](https://github.com/cyberchronos00/code-graph/issues/26)).

## [0.5.0] - 2026-10-02

### Added

- Swift support (heuristic tree-sitter layer, `pip install tree-sitter-swift`, no Xcode needed): classes, structs,
  enums, actors, protocols and extensions, functions and methods with name-based call resolution; Vapor routes with
  `grouped` / `group` prefixes, middleware guards and `RouteCollection` handlers; URLSession and Alamofire endpoints
  for `cg link`; SwiftUI (`NavigationLink`, `.navigationDestination`, `.sheet`, `TabView`, `WindowGroup`) and UIKit
  navigation as pages; `@main`, app-delegate, view-controller and background-task entry points; XCTest tests;
  `#if os(...)` blocks as platform conditions. `cg coverage` reports Swift as heuristic. New sample
  `examples/bookstore-ios` links to the Django sample
  ([#10](https://github.com/cyberchronos00/code-graph/issues/10)).
- Kotlin support (heuristic tree-sitter layer, `pip install tree-sitter-kotlin`): classes, objects, interfaces,
  top-level / extension functions and methods with name-based call resolution; Ktor (`routing` / `route` /
  `authenticate`) and Spring (`@RestController`, `@GetMapping` ..., `@PreAuthorize` / `@Secured`) routes and guards,
  `@Scheduled` and message listeners; Retrofit, Ktor client and OkHttp endpoints for `cg link`; Compose Navigation
  pages and `navigate(...)`; AndroidManifest components and deep links, WorkManager workers; KMP source sets as platform
  conditions and `expect` → `actual`. `cg coverage` reports Kotlin as heuristic. New sample `examples/bookstore-android`
  links to the Django sample.
- Framework presets and a fuller project config: each detected language and framework applies a curated preset
  (auth and secret guards for Laravel, Django, DRF, django-ninja, NestJS, Next.js, Express-style servers and Nuxt;
  shared skip lists used by every plugin and the coverage scan), recorded in the index stats; `.cg.yaml` adds
  `exclude`, `skip_dirs`, `frameworks`, `auth` / `secret` patterns, `gates`, `plans` and `viz.presets`; `cg config
  show|validate` lists every effective value with its source; `routes` names the rule behind each auth guard; starter
  queries derived from the graph (`cg starters`, MCP `starters`, the visual view's preset menu)
  ([#6](https://github.com/cyberchronos00/code-graph/issues/6)).
- Generated, copied and vendored files are detected and kept out of the graph by default: `.gitattributes`
  `linguist-generated` / `linguist-vendored`, Capacitor `webDir` copies under `android/` and `ios/` (each mapped to
  its source file), Cordova `platforms/*/www`, `.openapi-generator/FILES`, `.nuxt` / `.next` / `.svelte-kit` build
  output, Flutter plugin registrants and `ephemeral/`, `*.g.dart` / `*.pb.go` / `*_pb2.py`-style names and "generated
  by ... do not edit" header banners; `cg coverage` lists them by reason, `.cg.yaml` `generated.paths` / `vendored` /
  `keep` adjust the rules, and `--include-generated` indexes them labelled `attrs.generated` with `COPY_OF` edges from
  copies to their sources, shown as "(generated)" / "(copy of ...)" in `impact`
  ([#8](https://github.com/cyberchronos00/code-graph/issues/8)).
- Platform-specific code: symbols and references under Rust `#[cfg]` / `cfg!`, C / C++ `#if` platform macros and
  `win/` / `unix/` paths, Dart `Platform.isX` / `kIsWeb` / conditional imports and React Native `Platform.OS` /
  `Platform.select` / `.ios.ts` files carry the targets they are built for; variants are linked to each other;
  `--platform TARGET` on `reaches`, `impact`, `downstream`, `path`, `routes` and `search` (MCP `platform`, with the
  filter and the unevaluated conditions in every reply); `cg platforms [divergence]` and MCP `platforms` /
  `platform_divergence` list targets, variants that leave a target uncovered, API differences and references to
  code not built on a target; per-target coverage; `.cg.yaml` `platforms`
  ([#7](https://github.com/cyberchronos00/code-graph/issues/7)).

### Changed

- Coverage counts no longer include generated and copied files, and the `book.g.dart` nodes of `bookstore-flutter`
  are excluded by default (90 / 171 → 87 / 164 nodes / edges)
  ([#8](https://github.com/cyberchronos00/code-graph/issues/8)).
- Rust exact mode adds calls into items gated for another target (`via: cfg-inactive`), and a `#[cfg]` on a match
  arm gates the whole arm; C / C++ heuristic calls to a function defined once per `#if` branch link every definition;
  React Native imports resolve through the platform suffixes
  ([#7](https://github.com/cyberchronos00/code-graph/issues/7)).

### Fixed

- Starter queries and `cg routes --writes` stay fast on large connected graphs: the route report walks the graph
  once for all write targets (a single multi-target traversal limited to what the routes reach) instead of one
  recursive query per table, with the same output. saleor: starters 57.5 s → 0.4 s, index 124.4 s → 68.3 s. The starters
  keep to their 20 s budget: one that does not finish in time is left out and listed in `starters_skipped`
  ([#25](https://github.com/cyberchronos00/code-graph/issues/25)).
- The test suite skips the tests that index PHP code when the PHP extractor's Composer dependencies are not
  installed (as it already did for the TypeScript extractor), so a fresh checkout reports skips instead of failures
  ([#10](https://github.com/cyberchronos00/code-graph/issues/10)).
- Python calls through a collection follow copies and helper-built collections: elements of `copy.copy()` /
  `copy.deepcopy()` / `.copy()`, of the value a project function returns and of a constant key of a returned dict
  or tuple (`plan = setup(); for p in plan["plugins"]: p.index()`) keep their types, so `impact` and `tests` reach the
  callers again when a codebase makes fresh copies of a plugin list
  ([#9](https://github.com/cyberchronos00/code-graph/issues/9)).
- Routers, apps and controllers built inside TypeScript test files (`*.spec.ts`, `*.test.ts`, `test/`,
  `__tests__/`) no longer become application routes for the Express / Koa / Fastify / Hono, NestJS and Next.js
  layers; the files stay indexed as tests (immich `server/`: 295 routes, all guarded, instead of 297 with 2 test
  routes) ([#19](https://github.com/cyberchronos00/code-graph/issues/19)).
- A pytest `testpaths` entry that names the application package (`testpaths = ["app"]`, as in saleor) no longer
  turns the whole package into test code: only the files pytest collects there, `conftest.py` and `tests/`
  directories are tests, so `impact`, `reaches` and the starter queries work on such projects again
  ([#18](https://github.com/cyberchronos00/code-graph/issues/18)).
- Indexing several projects in one process (the MCP `index` tool called again, scripts) gives the same graph as a
  fresh run: every run uses its own plugin instances, so the PHP gate predicates of one project no longer land in the
  next project's graph, and the Express route-key and Rust trait-method caches are rebuilt per project
  ([#9](https://github.com/cyberchronos00/code-graph/issues/9)).

## [0.4.0] - 2026-10-02

### Added

- Completeness reporting: `cg coverage` shows discovered vs indexed files per language and why the rest were not
  indexed (`parse_failed`, `skipped_oversize`, `unmapped`, `excluded`), lists source types no plugin reads yet, and
  `--all-files` lists every file per bucket ([#11](https://github.com/cyberchronos00/code-graph/issues/11)).
- Blind-spot detectors find route and handler registrations no plugin models (NestJS `applyDecorators` wrappers,
  dynamic Django `urlpatterns`, routes registered in loops, Python registration decorators and registries), with
  `file:line` samples ([#11](https://github.com/cyberchronos00/code-graph/issues/11)).
- `routes`, `impact`, `callers`, `reaches`, `tests_covering` and `plan_check` say when an answer may be partial, and
  every MCP reply carries a structured `completeness` object, so agents know when to fall back to normal search; new
  `callers` MCP tool ([#11](https://github.com/cyberchronos00/code-graph/issues/11)).
- Python source roots detected from the project layout (src/, lib/, packaging config, several package roots,
  namespace packages, nested projects); `.cg.yaml` `python.source_roots` and `cg index --python-root`
  ([#13](https://github.com/cyberchronos00/code-graph/issues/13)).
- `cg coverage` and the MCP `coverage` tool list Python source roots with their origin and module counts, and flag
  missing configured roots and files outside every root ([#13](https://github.com/cyberchronos00/code-graph/issues/13)).
- Python entry points and function references: `__main__` blocks, `__main__.py` and packaging entry points
  (PEP 621, Poetry, flit, setup.cfg, setup.py) as `script` entry nodes; MCP tools and click / typer / Flask CLI
  commands as entry points; functions used in dispatch tables, as callbacks or through decorators get `REFERENCES_FN`
  edges, and calls through dispatch tables resolve, so `impact` reaches them
  ([#14](https://github.com/cyberchronos00/code-graph/issues/14)).
- Python tests: pytest and unittest cases (Django / DRF TestCase included) are test nodes with their framework,
  parametrize values and marks; fixtures are followed through `conftest.py` chains, autouse, `usefixtures` and
  `pytest_plugins`; Django, DRF, FastAPI and Flask test-client requests are found and Django / DRF / django-ninja ones
  link to their routes. `cg tests` lists Python tests, the header and `cg coverage` count test cases per framework,
  and test code never counts as a caller in `impact` / `callers` / `reaches`
  ([#15](https://github.com/cyberchronos00/code-graph/issues/15)).

### Changed

- Each Python file gets one canonical module name, chosen by the project's own imports; other importable names stay
  aliases ([#13](https://github.com/cyberchronos00/code-graph/issues/13)).
- Edges made by Python test code (calls, references, collection calls) are `TEST_CALLS` / `TEST_USES`, as for PHP and
  TypeScript, so Python `CALLS` counts now cover application code only
  ([#15](https://github.com/cyberchronos00/code-graph/issues/15)).

### Fixed

- Django: DRF router route names use the queryset model as default basename and `@action(url_name=...)`, and
  `include("app.urls")` takes the included module's `app_name` as namespace, so `reverse("app:name")` lookups match
  ([#15](https://github.com/cyberchronos00/code-graph/issues/15)).
- The TypeScript, Dart and SCIP (Rust, C / C++) caches are keyed by file content instead of size and mtime, so an
  edit that keeps the file size and timestamp is always re-indexed. Caches from older versions are discarded
  automatically ([#12](https://github.com/cyberchronos00/code-graph/issues/12)).

## [0.3.0] - 2026-10-01

### Added

- Laravel broadcast channels: `Broadcast::channel` callbacks as entry points, the channel auth route, `broadcastOn()`
  channel names, and Echo / pusher-js subscriptions in the frontend linked across repos; new `channels` query and
  MCP tool.
- Tests as first-class nodes: PHPUnit, Pest, Vitest, Jest, Playwright and Cypress tests are indexed with
  non-propagating `TEST_*` edges, so they show which code they cover without counting as callers; new `tests`
  query and `tests_covering` MCP tool.
- Routes behind signature or shared-secret middleware are marked `SECRET-CHECKED` in `routes`.
- Frontend base URLs from `runtimeConfig` / env are folded into endpoint paths, so more HTTP calls link to their
  backend routes.
- `api-calls` accepts `*` globs.
- Animated preview of the visual view in the README.

### Changed

- Nuxt apps are found at the repo root, in `app/` or in `src/`, and clean checkouts without a `.nuxt/` directory
  index fully.
- `coverage` works on linked (multi-repo) graphs.

### Fixed

- A dangling symlink skips only that file instead of dropping the whole language from the index.

## [0.2.0] - 2026-10-01

### Added

- Python / Django plugin (including django-ninja, DRF, Channels and Celery) and Dart / Flutter plugin, with
  cross-repo checks of request and response fields.
- NestJS, Next.js and Express / Fastify / Koa / Hono layers, and plain JavaScript.
- Rust, C and C++ through SCIP indexers (exact mode) with a tree-sitter fallback.
- `routes` query: middleware, guards and auth per route, filterable to routes that write data without auth; it also
  names routes a stricter `min_confidence` hides.
- Per-language coverage (exact / heuristic / skipped / unsupported) via `cg coverage` and the MCP `coverage` tool;
  empty replies carry a coverage note telling agents when to fall back to normal search.
- `impact` lists external snapshot clients from the plans directory.
- Flags parameters a page sends that a request helper drops.
- Demo videos (setup, terminal, visual view, a Cursor CLI agent over MCP, and the same agent without code-graph with
  a measured comparison) and Cursor CLI setup docs.

### Changed

- A missing language toolchain no longer fails the index; that language is reported by `coverage` instead.
- `search` matches middleware and guard names, and empty results explain why.
- MCP `index` on a combined graph re-indexes every repo by default, maps a root to its repo(s), and refuses unknown
  roots and empty results.
- `plan_check` returns a compact summary; MCP replies use repo-relative paths.

### Fixed

- Malformed plan items and invalid plan YAML are reported instead of crashing.

## [0.1.0] - 2026-10-01

First open-source release.

### Added

- Deterministic code graph for Laravel (PHP) and Nuxt / Vue (TypeScript): calls with type inference, routes and
  middleware, Eloquent models, tables and columns, migrations, DB connections, config / env, commands, scheduler,
  jobs, events and listeners, Filament panels, Nuxt pages, layouts, auto-imports, components and Pinia stores. Every
  edge carries `file:line` evidence and a confidence level (`exact`, `resolved`, `heuristic`).
- `link` matches frontend HTTP calls (fetch, `$fetch`, axios) to backend routes across repos.
- CLI queries: `reaches`, `impact`, `downstream`, `path`, `writers`, `siblings`, `node`, `stats`, `api-calls`,
  `resolutions`, grouping answers into runtime, operator-only and gated callers.
- Gate scenarios for feature flags, and value facts (request keys, settings, fallback chains).
- SCIP import for other languages (experimental).
- MCP server so AI agents can ask the same questions.
- Local visual view (`serve`) and static export (`viz-export`).
- Planned-change layer: describe a change in `plans/*.yaml` and check it for completeness and conflicts, with
  verify mode, baselines and a plan overlay in the visual view.
- Fictional bookstore sample apps, an example plan, `scripts/reproduce.sh`, docs, MIT license, contributing guide
  and security policy.

[Unreleased]: https://github.com/cyberchronos00/code-graph/compare/v0.5.0...HEAD
[0.5.0]: https://github.com/cyberchronos00/code-graph/compare/v0.4.0...v0.5.0
[0.4.0]: https://github.com/cyberchronos00/code-graph/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/cyberchronos00/code-graph/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/cyberchronos00/code-graph/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/cyberchronos00/code-graph/releases/tag/v0.1.0
