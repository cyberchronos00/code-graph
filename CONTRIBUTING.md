# Contributing to code-graph

Thanks for helping. Bug reports, small fixes, new framework rules and new language plugins are all welcome.

## Dev setup

Prerequisites: Python 3.11+, PHP 8.2+ with Composer 2, Node.js 20+ (see the [README](README.md#quickstart-about-2-minutes-on-the-bundled-sample-apps)).

```bash
git clone https://github.com/cyberchronos00/code-graph.git && cd code-graph
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"     # cg and cg-mcp from this checkout, plus pytest
(cd codegraph/plugins/php/extractor && composer install)        # extractor deps in the checkout (the tests use them)
(cd codegraph/plugins/ts/extractor && npm ci)
```

An installed cg (not a checkout) puts the extractor dependencies in the user cache instead
(`codegraph/core/extractors.py`, `cg setup`); `cg doctor` shows which directory is in use.

Generated files (`out/`, `*.db`, plan baselines, `node_modules/`, `vendor/`) are gitignored.

## Running the tests

```bash
.venv/bin/python -m pytest -q tests/
```

The suite indexes the sample apps in `examples/` and the small fixtures in `tests/` into temp DBs. It needs no
network and no database, and runs in about 10 seconds. It must leave `git status` clean: generated output goes to
temp dirs or `out/`. The one tracked generated file, `docs/mcp/sample_outputs.md`, is deterministic and only rewritten
when its content changes. If your change alters MCP output, commit the regenerated file.

cg supports Python 3.11+ (`requires-python`), so syntax and standard-library APIs newer than 3.11 (for example a
backslash or a same-quote string inside an f-string expression, `type X = ...`, `itertools.batched`) break it.
`tests/test_python_compat.py` checks the syntax on any interpreter and byte-compiles the package with a Python 3.11
when one is installed; before a release, also run the suite under 3.11:

```bash
uv run --isolated --python 3.11 --extra dev python -m pytest -q tests/     # a temporary 3.11 env
```

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
   `codegraph/plugins/stubs/plugins.py` is often enough to start. For a first-class plugin that combines a SCIP
   indexer with a tree-sitter syntax layer, `codegraph/plugins/rust/` and `codegraph/plugins/cfamily/` (on top of
   the shared `codegraph/plugins/native/` helpers) are the templates.
4. **Completeness:** leave a per-file report in `self.file_report` (`seen`, `parse_failed`, `skipped_oversize`,
   `unmapped`, `excluded`) so `cg coverage` can tell discovered from indexed files, and add a detector to
   `codegraph/blindspots.py` for registration patterns the plugin does not model (with a positive and a negative
   fixture), so answers can say where they may be partial ([docs/completeness.md](docs/completeness.md)).
5. Register the plugin in `LANGUAGE_PLUGINS` / `FRAMEWORK_PLUGINS` in `codegraph/indexer.py` and, if needed, add
   marker files to `codegraph/core/detect.py`.
6. Add a **small fixture** under `tests/` (a few files, written from scratch) and a test that asserts the exact edges
   you expect, with their `file:line` and confidence.

## Pull request expectations

- **Tests pass** and `git status` is clean after running them.
- **Deterministic edges only.** Every edge comes from a parser fact, the type checker or a named rule, so results are reproducible.
- **Evidence and honest confidence.** `exact` if syntactically certain, `resolved` if it needed type or name
  resolution, `heuristic` for fallbacks.
- **No real-world code in fixtures.** Write fixtures from scratch; don't copy code from private or third-party
  projects.
- **Changelog entry.** Add a line under `## [Unreleased]` in [CHANGELOG.md](CHANGELOG.md) (Added / Changed /
  Fixed / Removed), linking the issue if there is one.
- **Docs follow the code.** Update the README or the relevant file in `docs/` when you change behaviour, a command or
  output.
- Keep PRs focused. For a larger change, open an issue first to agree on the approach.

## Releasing

Versions follow [Semantic Versioning](https://semver.org/); the history is in [CHANGELOG.md](CHANGELOG.md).

1. Every change adds its line under `## [Unreleased]` in the same commit.
2. To release `X.Y.Z`: bump `__version__` in `codegraph/__init__.py` (the only place: `pyproject.toml` reads it, and
   `cg doctor` / `cg --version` report it), rename `## [Unreleased]` to
   `## [X.Y.Z] - YYYY-MM-DD` (UTC date), add a fresh empty `## [Unreleased]` above it, and update the compare links at
   the bottom of the changelog.
3. Commit, then create an annotated tag and push it: `git tag -a vX.Y.Z -m "vX.Y.Z"` and `git push origin vX.Y.Z`.
4. Create the GitHub release from the tag, titled `vX.Y.Z`, with that version's changelog section as the notes:
   `gh release create vX.Y.Z --title vX.Y.Z --notes-file <section.md>`.
5. Check the install path from the tag in a clean environment: `sh install.sh --version vX.Y.Z` (or
   `uv tool install git+https://github.com/cyberchronos00/code-graph@vX.Y.Z`), then `cg doctor`. Users update with
   `uv tool upgrade codegraph`, `pipx upgrade codegraph` or `install.sh --update`.
