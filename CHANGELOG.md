# Changelog

All notable changes to code-graph are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html). While the version is 0.x, minor releases may change
commands, output and the graph schema; such changes are listed under **Changed**.

## [Unreleased]

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

[Unreleased]: https://github.com/cyberchronos00/code-graph/compare/v0.4.0...HEAD
[0.4.0]: https://github.com/cyberchronos00/code-graph/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/cyberchronos00/code-graph/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/cyberchronos00/code-graph/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/cyberchronos00/code-graph/releases/tag/v0.1.0
