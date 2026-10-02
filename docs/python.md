# Python source roots

cg names every Python module the way the interpreter imports it, so `from core.policy import is_allowed` lands on
`lib/core/policy.py` whether the project uses a flat layout, a `src/` layout, a `lib/` directory put on `sys.path` by
a launcher, several packages side by side, namespace packages or nested projects. The Python plugin runs whenever the
repository has `.py` files outside dependency, build and cache directories; no marker file or flag is needed.

## Detection

A **source root** is a directory whose contents are importable by top-level name. cg collects them from this
evidence, and records the reason for each:

| evidence | example | reason shown |
|---|---|---|
| the indexed root | `app/views.py` -> `app.views` | `indexed root` |
| packaging config at the root or in a nested project | `pyproject.toml`: `[tool.setuptools] package-dir = {"" = "code"}`, `[tool.setuptools.packages.find] where`, `[tool.hatch.build(.targets.*)] packages / sources`, `[tool.poetry] packages = [{from = ...}]`, `[tool.pdm.build] package-dir`, `[tool.maturin] python-source`; `setup.cfg` `[options] package_dir`, `[options.packages.find] where`; `setup.py` literal `package_dir=` / `find_packages("src")` | `pyproject.toml [tool.setuptools] package-dir` |
| conventional directories that are not packages themselves | `src/`, `lib/`, `python/` (at the root and in nested projects) | `lib/ directory` |
| nested projects (depth 4 or less) | a directory holding `pyproject.toml`, `setup.py`, `setup.cfg` or `manage.py` | `nested project (backend/manage.py)` |
| namespace packages (PEP 420) | `plugins/acme/core/base.py` with no `__init__.py`, imported as `acme.core.base` -> `plugins/` | `namespace package acme (imported as acme.core)` |
| the parent of every top-level package | `services/billing/billing/__init__.py` -> `services/billing/` | `parent of top-level package billing` |

A `package-dir` entry that maps a package to another directory (`{"core" = "lib"}`) makes `lib/` hold package `core`.

## Module names

A file can be importable under several roots (`src/acme/api.py` is `acme.api` under `src/` and `src.acme.api` under
the indexed root). Its node gets one canonical name, chosen by the project's own imports: the root whose top-level name
the imports use most wins, and on a tie the deepest root. The other names stay aliases, so imports written either way
resolve to the same node. `cg coverage` reports the modules where the imports used more than one name.

Two package trees can claim the same name, typically `tests/` in each project of a monorepo. The tree with the most
import evidence keeps the name (then a tree without any other importable name, then the shallower root); the others
are named by their path from the indexed root (`svc-b.tests.conftest`), which keeps them in the graph with their
calls, and keeps relative imports inside each tree resolving. Coverage lists how many files were renamed this way.

Files whose path is not an importable name (`my-scripts/run.py`, `alembic/versions/1a2b_add.py`) are listed in the
`unmapped` bucket ([completeness.md](completeness.md)).

## Configuration

Set the roots once in the project config file, `.cg.yaml` (or `.cg.yml`) at the indexed root. Configured roots replace
detection; `.` is the indexed root:

```yaml
version: 1
python:
  source_roots: [lib, tools/scripts]
```

`cg index ROOT --python-root lib --python-root tools/scripts` does the same for one run and takes precedence over the
file. The MCP `index` tool re-reads `.cg.yaml` on every re-index and keeps roots given with `--python-root`.
Paths must stay inside the indexed root. A file cg cannot use stops the index with a message naming the file and key
(`.cg.yaml: python.source_roots[0]: '../x' must stay inside the indexed root`); top-level keys this version does not
read are kept and listed in the index stats (`stats.config.ignored_keys`).

## Coverage output

`cg coverage` and the MCP `coverage` tool list the roots with their origin and the number of modules each holds, and
warn about configured roots that do not exist and files outside every root:

```console
$ cg coverage --db out/proj.db
coverage proj: python 3 exact
  python source roots: lib/ (detected: lib/ directory; parent of top-level package core, 3 modules)
every source file cg found is indexed; edges still carry their own exact / resolved / heuristic label.
```

With configured roots, files outside them are `unmapped`, with a hint naming the setting:

```console
coverage conf: not fully covered: python 5 discovered, 4 indexed (exact parser): 1 unmapped
  python source roots: lib/ (configured: configured in .cg.yaml, 2 modules); vendored/ (configured: configured in .cg.yaml, 2 modules)
  python: 5 files (.py 5) 4 indexed, 1 unmapped
    unmapped: scripts/release.py
    fix: unmapped .py files are outside the configured source roots (lib/, vendored/): add their directories to python.source_roots in .cg.yaml, or drop the setting to use detection
```

A layout that only uses the indexed root keeps the short output (`--all-files` shows the root too). In the index stats
the Python plugin records `roots_mode` (`detected`, `configured`, `flag`), `source_roots` (`path`, `origin`, `why`,
`modules`, and `package` for a mapped directory), `roots_warnings`, `roots_ambiguous` and `module_name_collisions`;
MCP `coverage(json_output=true)` adds them as `python_source_roots`.

## Validation

Measured on shallow clones of public projects (default branch, October 2026), before and after source-root detection:

| Project | Layout | Python files indexed | IMPORTS edges | CALLS edges | Python plugin time |
|---|---|---|---|---|---|
| fastapi/full-stack-fastapi-template | nested project (`backend/pyproject.toml`, package `app`) | 40 / 43 -> 40 / 43 | 0 -> 70 | 22 -> 180 | 0.1 s -> 0.1 s |
| open-telemetry/opentelemetry-python | monorepo of ~40 hatch projects, `src/` layouts, `opentelemetry` namespace package | 56 / 758 -> 741 / 758 | 1 -> 2,294 | 69 -> 11,627 | 0.8 s -> 3.7 s |
| ansible/ansible | `lib/` layout (`[tool.setuptools.packages.find] where`), `ansible_collections` namespace packages in tests | 1,564 / 1,839 -> 1,690 / 1,839 | 925 -> 4,440 | 9,935 -> 21,772 | 9.4 s -> 9.1 s |
| pallets/flask | `src/` layout, example apps as nested projects | 83 / 83 -> 83 / 83 | 299 -> 315 | 1,379 -> 1,396 | 0.4 s -> 0.5 s |
| pytest-dev/pytest | `src/` layout with two packages (`_pytest`, `pytest`) | 270 / 274 -> 270 / 274 | 1,473 -> 1,473 | 14,589 -> 14,589 (same graph) | 4.2 s -> 4.0 s |
| netbox-community/netbox | Django project in `netbox/` (`manage.py`) | 980 / 1,290 -> 980 / 1,290 | 4,346 -> 4,346 | 20,439 -> 20,439 (same graph) | 13.4 s -> 13.1 s |

Spot check: 21 IMPORTS edges sampled at random from the new graphs of ansible, opentelemetry-python and the FastAPI
template, compared by hand with the import line they cite: 21/21 land on the right file. The files that stay out are
paths that are not importable names (`hacking/test-module.py`, `docs/examples/fork-process-model/...`, Alembic
revisions such as `versions/1a31ce608336_add_cascade.py`); netbox's 310 are its migrations (excluded by default).
