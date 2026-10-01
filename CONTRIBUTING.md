# Contributing to code-graph

Thanks for helping. Bug reports, small fixes, new framework rules and new language plugins are all welcome.

## Dev setup

Prerequisites: Python 3.11+, PHP 8.2+ with Composer 2, Node.js 20+ (see the [README](README.md#quickstart-about-2-minutes-on-the-bundled-sample-apps)).

```bash
git clone https://github.com/cyberchronos00/code-graph.git && cd code-graph
python3 -m venv .venv && .venv/bin/pip install "mcp>=2.2" pyyaml pytest protobuf
(cd codegraph/plugins/php/extractor && composer install)
(cd codegraph/plugins/ts/extractor && npm ci)
```

Generated files (`out/`, `*.db`, plan baselines, `node_modules/`, `vendor/`) are gitignored.

## Running the tests

```bash
.venv/bin/python -m pytest -q tests/
```

The suite indexes the sample apps in `examples/` and the small fixtures in `tests/` into temp DBs. It needs no
network and no database, and runs in about 10 seconds. It must leave `git status` clean: generated output goes to
temp dirs or `out/`. The one tracked generated file, `docs/mcp/sample_outputs.md`, is deterministic and only rewritten
when its content changes. If your change alters MCP output, commit the regenerated file.

`scripts/reproduce.sh` runs the full end-to-end pass (index, link, queries, plan check, HTML views, tests).

## Adding a language or framework plugin

Read [docs/architecture.md](docs/architecture.md) first (invariants, codemap, plugin interface).

1. **Language plugin:** subclass `LanguagePlugin` (`codegraph/core/plugin.py`) with `detect(project)` and
   `index(project, builder, frameworks)`. Emit nodes with `builder.add_node(kind, key, …)` and edges with
   `builder.add_edge(src, dst, kind, file=, line=, confidence=)`. Reuse the existing node and edge kinds in
   `codegraph/core/model.py` where they fit; add a new kind there (with its `propagates` flag and a description) only
   when none does.
2. **Framework plugin:** subclass `FrameworkPlugin`. Use `register_hooks` for type rules and fact handlers that must
   run during resolution, and `contribute` for framework nodes and edges (routes, entry points, …) afterwards.
3. **SCIP instead:** if a SCIP indexer exists for the language, a `ScipIndexerPlugin(...)` entry in
   `codegraph/plugins/stubs/plugins.py` is often enough to start.
4. Register the plugin in `LANGUAGE_PLUGINS` / `FRAMEWORK_PLUGINS` in `codegraph/indexer.py` and, if needed, add
   marker files to `codegraph/core/detect.py`.
5. Add a **small fixture** under `tests/` (a few files, written from scratch) and a test that asserts the exact edges
   you expect, with their `file:line` and confidence.

## Pull request expectations

- **Tests pass** and `git status` is clean after running them.
- **Deterministic edges only.** Every edge comes from a parser fact, the type checker or a named rule. No model output.
- **Evidence and honest confidence.** `exact` if syntactically certain, `resolved` if it needed type or name
  resolution, `heuristic` for fallbacks.
- **No real-world code in fixtures.** Write fixtures from scratch; don't copy code from private or third-party
  projects.
- **Docs follow the code.** Update the README or the relevant file in `docs/` when you change behaviour, a command or
  output.
- Keep PRs focused. For a larger change, open an issue first to agree on the approach.
