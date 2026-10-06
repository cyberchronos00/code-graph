<div align="center">

# code-graph

**See everything a change affects before you make it: a local, deterministic dependency graph of routes, calls, tables and config, with `file:line` evidence for every hop.**

For you and your AI agent (CLI + MCP). No LLM in the graph.

[![PyPI](https://img.shields.io/pypi/v/cg-code-graph)](https://pypi.org/project/cg-code-graph/) [![Downloads](https://img.shields.io/pepy/dt/cg-code-graph)](https://pepy.tech/project/cg-code-graph) [![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-3776AB)](https://pypi.org/project/cg-code-graph/) [![License: MIT](https://img.shields.io/badge/license-MIT-blue)](LICENSE) [![Docs](https://img.shields.io/badge/docs-code--graph-93b600)](https://code-graph.cyberchronos00.workers.dev/)

[Docs](https://code-graph.cyberchronos00.workers.dev/) · [Quick start](https://code-graph.cyberchronos00.workers.dev/docs/quickstart) · [CLI](https://code-graph.cyberchronos00.workers.dev/docs/cli) · [MCP](https://code-graph.cyberchronos00.workers.dev/docs/mcp) · [Changelog](CHANGELOG.md)

[![cg visual view: the graph around the warehouse connection, a selected node with its evidence paths and source, then the planned-change overlay for the preorders plan](docs/media/cg-view-preview.gif)](docs/media/cg-view-demo.mp4)

</div>

## Quick start

```bash
pipx install cg-code-graph          # or: uv tool install cg-code-graph
cg index . --db out/graph.db        # prints starter queries for your repo
cg install --host cursor    # or claude, codex, vscode, …: registers the MCP server
cg reaches table:orders --db out/graph.db   # any table, connection or config key
```

Try it on a bundled sample, link a frontend to a backend, or connect an agent over MCP: [Quick start guide](https://code-graph.cyberchronos00.workers.dev/docs/quickstart). Other install options (install.sh, Windows, exact-mode toolchains): [Install](https://code-graph.cyberchronos00.workers.dev/docs/install).

`cg reaches table:store_books` on the bundled Django sample:

```text
dependents: 18 nodes  (code: 6, entry points: 9, other: 3)
== RUNTIME (reached from http_route / ... / queue_job / listener): 5 functions/methods
    catalog.api.create_book  depth=1 conf=resolved  http_route(1)
        path: function:catalog.api.create_book
          -WRITES_TABLE[resolved @ catalog/api.py:27]-> table:store_books
    catalog.signals.review_saved  depth=3 conf=resolved  listener(1)
        path: function:catalog.signals.review_saved
          -DISPATCHES[exact @ catalog/signals.py:12]-> job:catalog.tasks.recompute_rating
          -HANDLED_BY[exact @ catalog/tasks.py:13]-> function:catalog.tasks.recompute_rating
          -READS_TABLE[resolved @ catalog/tasks.py:14]-> table:store_books
```

[![Terminal demo: cg indexes the bundled Laravel and Nuxt sample apps, links them, then runs reaches, path, impact and plan check](docs/media/cg-terminal-demo.gif)](docs/media/cg-terminal-demo.mp4)

## Supported languages

| Language | Frameworks and facts | Calls | Docs |
|---|---|---|---|
| ![Python](https://img.shields.io/badge/Python-3776AB?logo=python&logoColor=white) | Django (+ DRF, django-ninja), FastAPI / Starlette, Flask, Celery | exact (stdlib `ast`) | [Python](https://code-graph.cyberchronos00.workers.dev/docs/python) |
| ![TypeScript](https://img.shields.io/badge/TypeScript-3178C6?logo=typescript&logoColor=white) ![JavaScript](https://img.shields.io/badge/JavaScript-F7DF1E?logo=javascript&logoColor=black) | Nuxt / Vue, NestJS, Next.js, Astro, Express, Fastify, Koa, Hono, Elysia | exact (TypeScript compiler API) | [TS frameworks](https://code-graph.cyberchronos00.workers.dev/docs/ts-frameworks) |
| ![PHP](https://img.shields.io/badge/PHP-777BB4?logo=php&logoColor=white) | Laravel (routes, middleware, queues, broadcasting, outbound HTTP) | exact (nikic/php-parser; needs PHP 8.2+) | [PHP](https://code-graph.cyberchronos00.workers.dev/docs/php) |
| ![Dart](https://img.shields.io/badge/Dart-0175C2?logo=dart&logoColor=white) | Flutter | exact (Dart analyzer; needs the Dart SDK) | [Platforms](https://code-graph.cyberchronos00.workers.dev/docs/platforms) |
| ![Kotlin](https://img.shields.io/badge/Kotlin-7F52FF?logo=kotlin&logoColor=white) | Android, Kotlin Multiplatform, Spring | heuristic; exact with scip-java (opt-in) | [Kotlin](https://code-graph.cyberchronos00.workers.dev/docs/kotlin) |
| ![Java](https://img.shields.io/badge/Java-ED8B00?logo=openjdk&logoColor=white) | Spring (routes, guards, beans, tables, clients) | heuristic; exact with scip-java (opt-in) | [Java](https://code-graph.cyberchronos00.workers.dev/docs/java) |
| ![Swift](https://img.shields.io/badge/Swift-F05138?logo=swift&logoColor=white) | iOS / SwiftPM | heuristic; exact with the Swift index store (opt-in) | [Swift](https://code-graph.cyberchronos00.workers.dev/docs/swift) |
| ![Rust](https://img.shields.io/badge/Rust-000000?logo=rust&logoColor=white) ![C](https://img.shields.io/badge/C-A8B9CC?logo=c&logoColor=black) ![C++](https://img.shields.io/badge/C++-00599C?logo=cplusplus&logoColor=white) | crates / modules, entry points, FFI, `#[cfg]` / `#if` gates | heuristic; exact with rust-analyzer / scip-clang | [Rust, C and C++](https://code-graph.cyberchronos00.workers.dev/docs/native) |

Go and others: import a SCIP index with `cg index --scip FILE`. Run `cg doctor` to see exact / heuristic per language on your machine.

## What you get

- `reaches`, `impact`, `path`: every caller, route, job and page that reaches a table, config key or method.
- `cg affected --base main`: the tests and entry points a change reaches (`--quiet` feeds your test runner).
- Across repos: `cg link` matches client HTTP calls (frontends and Laravel / Guzzle backends) to server routes.
- Route guards: `cg routes --writes --unguarded` lists routes that write with no auth guard.
- MCP server (`cg-mcp`) so agents query the graph instead of grepping.
- Visual view (`cg serve`) and plan checks (`cg plan check`) for planned changes.

## Docs

[Install](https://code-graph.cyberchronos00.workers.dev/docs/install) · [Quick start](https://code-graph.cyberchronos00.workers.dev/docs/quickstart) · [CLI](https://code-graph.cyberchronos00.workers.dev/docs/cli) · [MCP](https://code-graph.cyberchronos00.workers.dev/docs/mcp) · [Configuration](https://code-graph.cyberchronos00.workers.dev/docs/configuration) · [Architecture](https://code-graph.cyberchronos00.workers.dev/docs/architecture) · [Limitations](https://code-graph.cyberchronos00.workers.dev/docs/limitations)

[Changelog](CHANGELOG.md) · [Contributing](CONTRIBUTING.md) · [Security](SECURITY.md) · MIT [License](LICENSE)

More recordings: [docs/media](docs/media).
