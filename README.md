# code-graph

**A deterministic dependency graph for Laravel, Django, FastAPI, Flask, NestJS, Next.js, Express, Nuxt, Flutter, Rust, C and C++ codebases, so you (and your AI agent) can see everything a change touches before you make it.**

Ask "what depends on this table, connection, config key or method?" and get every caller, route, command and page that reaches it, each hop backed by `file:line` evidence. It runs locally on your source files. Answers are exact, deterministic and reproducible: the graph is built from parsers and the type checker, with no LLM in the graph. BloodHound-style: you walk real paths instead of grepping names.

> **Status:** beta. Laravel (PHP), Django, FastAPI / Starlette and Flask (Python), TypeScript/JavaScript (Nuxt/Vue, NestJS, Next.js, Express/Fastify/Koa/Hono), Flutter (Dart), Rust, C and C++ are supported natively; other languages can be imported through SCIP.

## Documentation

**[code-graph docs](https://code-graph.cyberchronos00.workers.dev/)** — install, CLI, MCP, architecture, schema, languages and limitations.

## Install

`cg` is a user-level tool (no sudo, no checkout). The PyPI package is [`cg-code-graph`](https://pypi.org/project/cg-code-graph/); the commands are `cg` and `cg-mcp`.

```bash
pipx install cg-code-graph    # or: uv tool install cg-code-graph
#   or the script (also installs exact-mode tools with --with c,rust,...):
#     curl -fsSL https://raw.githubusercontent.com/cyberchronos00/code-graph/main/install.sh | sh
#   or the latest commit: uv tool install git+https://github.com/cyberchronos00/code-graph
cg doctor                     # what indexes exact / heuristic on this machine
```

Update with `uv tool upgrade cg-code-graph`, `pipx upgrade cg-code-graph` or `install.sh --update`. Caches live under `~/.cache/codegraph`; `cg clean` removes them. Windows, options and per-language toolchains: [install](https://code-graph.cyberchronos00.workers.dev/).

## Quickstart

Clone the repo for the bundled sample apps (a Laravel API and a Nuxt web app). Python 3.11+. PHP 8.2+ and Composer for the Laravel sample; Node.js 20+ for the TypeScript samples.

```bash
git clone https://github.com/cyberchronos00/code-graph.git && cd code-graph
mkdir -p out
cg index examples/bookstore-api --name bookstore-api --gates examples/bookstore.gates.json --db out/api.db
cg index examples/bookstore-web --name bookstore-web --db out/web.db
cg link --backend out/api.db --frontend out/web.db \
        --backend-name bookstore-api --frontend-name bookstore-web --db out/graph.db

cg reaches connection:warehouse table:warehouse_stock --db out/graph.db
cg impact StockService::reserve --db out/graph.db
cg path page:/reports/:id table:orders --db out/graph.db
cg plan check preorders --plans-dir examples/plans --db out/graph.db
```

A workspace `.cg.yaml` can list several apps, including checkouts outside that directory, and `cg index` builds one combined graph. `cg link --repo` merges graphs you already indexed. Other samples in `examples/` (Nest, Next, Express, Django, Flutter, Rust, C, C++) index the same way. Queries, MCP (`cg-mcp --db out/graph.db`) and `cg serve` are in the [docs](https://code-graph.cyberchronos00.workers.dev/).

## Preview

[![cg visual view: the graph around the warehouse connection, a selected node with its evidence paths and source, then the planned-change overlay for the preorders plan](docs/media/cg-view-preview.gif)](docs/media/cg-view-demo.mp4)

[![Terminal demo: cg indexes the bundled Laravel and Nuxt sample apps, links them, then runs reaches, path, impact and plan check](docs/media/cg-terminal-demo.gif)](docs/media/cg-terminal-demo.mp4)

More recordings (setup, visual view, agent over MCP): [docs/media/](docs/media).

## Links

- [Documentation](https://code-graph.cyberchronos00.workers.dev/)
- [PyPI: cg-code-graph](https://pypi.org/project/cg-code-graph/)
- [GitHub releases](https://github.com/cyberchronos00/code-graph/releases)
- [Changelog](CHANGELOG.md) · [Contributing](CONTRIBUTING.md) · [Security](SECURITY.md) · [License: MIT](LICENSE)
