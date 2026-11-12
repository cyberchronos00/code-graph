# Cloud agent bootstrap

A clean cloud or CI machine has no `cg` on PATH and no MCP host config. Install the Python package, fix PATH, check freshness, read `cg doctor`, then register MCP without a prompt.

cg is a Python package on PyPI named `cg-code-graph`. It is not a Rust crate. `cargo install cg-code-graph` does not apply.

## Install

Prefer an installer that puts `cg` on PATH:

```console
uv tool install cg-code-graph
```

```console
pipx install cg-code-graph
```

With only pip:

```console
python -m pip install --user cg-code-graph
```

pip may install `cg`, `cg-mcp` and `cg-code-graph` into a user scripts directory that is not on PATH. Do not edit shell startup files from an agent. `cg doctor` prints one `export PATH="..."` line (on Windows, `set PATH=...`) when it can see that script. Until the directory is on PATH, start cg with the interpreter that installed it:

```console
python -m cg_code_graph --version
python -m cg_code_graph doctor
```

`python -m cg_code_graph` raises `ModuleNotFoundError` on a different interpreter than the one that ran pip. Use that interpreter, or the PATH line `cg doctor` printed.

## Freshness

```console
cg index . --db out/graph.db
```

Indexing records the git commit in the database and in `out/.cg-head`:

```text
graph.db <full sha>
```

The same sha is written to `out/graph.db.cg-head`. Compare it with `git rev-parse HEAD`. The same value means the graph matches HEAD. A different value means the graph is stale.

`cg refresh` builds the replacement database at `<db>.refresh.tmp` and renames it onto `<db>` (`os.replace`: an atomic swap on the same filesystem). `.cg-head` is written the same way: a sibling file whose name ends in `.tmp` is renamed into place. A reader sees the previous file or the new file.

The MCP `graphs` tool reports that commit and a freshness word:

| Word | Meaning |
|---|---|
| `fresh` | Recorded commit equals git HEAD |
| `stale` | Recorded commit differs from HEAD |
| `missing` | No `.cg-head` line for this database and no commit stored in it |
| `unknown` | The stamp, the database, or git could not be read |

`unknown` is a read failure. An absent stamp is `missing`, not `unknown`.

## Heuristic is not a broken install

`cg doctor` prints one row per language present in the project:

```text
  swift       heuristic   no Package.swift or .xcodeproj found (heuristic mode; ...)
```

`heuristic` means the tree-sitter layer indexed the files. `exact` means a compiler index ran. A heuristic Swift, Kotlin, Java, Rust or C/C++ row is expected when the toolchain or the build file is absent. The `fix:` line under that row is how to get exact mode. It does not mean the cg package failed to install. `cg doctor` also prints `Not a Rust crate` so the install stays on pip, pipx or uv.

## Compact queries

```console
cg search Name --db out/graph.db --kind function --limit 20
cg snippet Name --db out/graph.db --context 5 --max-lines 80
cg impact Name --db out/graph.db --max-depth 5 --no-paths
cg node Name --db out/graph.db --no-paths
cg readers table:ledger --db out/graph.db --no-paths
cg writers Ledger.total --db out/graph.db --no-paths
cg explore "who writes the ledger" --db out/graph.db --budget 1500 --max-symbols 3
```

Open a source file after `search` finds nothing for a computed property, a resource key, or a closure assignment. Those often have no symbol of their own.

An empty `cg detect` is not proof the tree has no sources. Check `cg coverage` and `cg doctor` before treating the repository as empty.

## MCP when no host is configured

```console
cg install --yes --project
```

`--yes` skips the confirmation prompt. When no MCP host config is present, it writes the Cursor project entry at `.cursor/mcp.json` and the agent rules. `--no-rules` skips the rules. `--dry-run` prints the edit and writes nothing. Pass `--host` for another host (`claude`, `codex`, `vscode`, and the others `cg install --help` lists).

The CLI works without `cg install`. Query with `cg` and `--db` when the MCP server is not registered.

More install options: [Install](install.md). First queries: [Quick start](quickstart.md).
