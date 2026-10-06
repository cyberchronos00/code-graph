# Installing and updating cg

cg is a Python package (Python 3.11+). The PyPI name is `cg-code-graph`; the commands are `cg` and `cg-mcp`. It installs as a user-level tool. No checkout and no sudo.

New here? [Quick start](quickstart.md) indexes a sample app and runs the first queries in a few minutes.

## Install

::tabs{default-value="0"}

:::tabs-item{label="uv"}

Recommended.

```console
uv tool install cg-code-graph
```

:::

:::tabs-item{label="pipx"}

If you already use pipx.

```console
pipx install cg-code-graph
```

:::

:::tabs-item{label="macOS / Linux"}

The install script picks uv or pipx.

```console
curl -fsSL https://raw.githubusercontent.com/cyberchronos00/code-graph/main/install.sh | sh
```

:::

:::tabs-item{label="Windows"}

Run this in PowerShell.

```console
irm https://raw.githubusercontent.com/cyberchronos00/code-graph/main/install.ps1 | iex
```

:::

:::tabs-item{label="Advanced"}

Latest commit or a tag. `pipx install` takes the same git URL.

```console
uv tool install git+https://github.com/cyberchronos00/code-graph
```

```console
uv tool install git+https://github.com/cyberchronos00/code-graph@vX.Y.Z
```

```console
install.sh --version vX.Y.Z
```

:::

::

### Virtual environment

Inside a venv, pip works too.

```console
pip install cg-code-graph
```

### cg doctor

```console
cg doctor
```

It lists the tools it found, whether extractor dependencies are installed, and for each language whether indexing is exact or heuristic. Pass a project path to check that project. The MCP `doctor` tool prints the same report.

### JavaScript runtime

TypeScript / JavaScript indexing needs Node.js 20+ or Bun (tested with Bun 1.4.2).
Lookup order: `CG_NODE`, then `node`, then `bun` on PATH.
Extractor dependencies install with `npm ci`, or `bun install` when npm is missing.
A Bun-only container (`oven/bun`) needs nothing else.
`cg doctor` names the runtime it found.

## Update and uninstall

::tabs

:::tabs-item{label="uv"}

Update.

```console
uv tool upgrade cg-code-graph
```

Uninstall.

```console
uv tool uninstall cg-code-graph
```

:::

:::tabs-item{label="pipx"}

Update.

```console
pipx upgrade cg-code-graph
```

Uninstall.

```console
pipx uninstall cg-code-graph
```

:::

:::tabs-item{label="script"}

Update, shell then PowerShell.

```console
install.sh --update
```

```console
install.ps1 -Update
```

Uninstall, shell then PowerShell.

```console
install.sh --uninstall
```

```console
install.ps1 -Uninstall
```

:::

::

The old package name `codegraph` (through v0.9.0) is removed by the install scripts. By hand: uninstall `codegraph`, then install `cg-code-graph`.

## Upgrading to 0.17

0.17 renames the import package to `cg_code_graph`. Two other PyPI projects (`codegraph` and `codegraph-py`) already ship a top-level `codegraph` package, and colbymchenry/codegraph uses the `CODEGRAPH_*` environment names, so this project no longer uses either.

- Environment variables are `CG_*` (`CG_JOBS`, `CG_CACHE`, …). `CODEGRAPH_*` still works in 0.17.x and 0.18.x and prints one deprecation warning per name. Those names are removed in 0.19.0. `CODEGRAPH_CACHE_DIR` is read as `CG_CACHE`.
- The cache root is `~/.cache/cg` (`%LOCALAPPDATA%\cg` on Windows, or `$XDG_CACHE_HOME/cg`). The first command that uses the default root moves `~/.cache/codegraph` there and drops `swift-build/` (SwiftPM build directories embed absolute paths, so the next Swift index rebuilds). Extractor installs, SCIP output and fact caches move with it. Set `CG_CACHE` to keep a cache where it is.
- `python -m cg_code_graph.cli` replaces `python -m codegraph.cli`. `python -m cg_code_graph` works the same way. The `cg` and `cg-mcp` commands are unchanged. MCP entries that `cg install` wrote with an absolute `cg-mcp` keep working. An entry that still runs `python -m codegraph.mcp_server` is reported by `cg doctor`; re-run `cg install`.
- Git hooks that still run `python -m codegraph.cli` stop refreshing until you re-run `cg hooks install`. `cg hooks status` and `cg doctor` name an outdated hook.

## Elsewhere

- Extractors install on the first index that needs them, or with `cg setup`. Cache and prune: `cg setup -h` and [clean](cli.md#clean).
- Exact mode for Rust, C / C++, Kotlin and Swift needs optional indexers: [Rust, C and C++](native.md), [Kotlin](kotlin.md), [Swift](swift.md). Java heuristic mode needs no JDK; `cg setup java` / `install.sh --with java` print the JDK 17+ hint for scip-java ([Java](java.md)).
- What the install scripts do: `install.sh --help`. Working on a checkout of cg: [Contributing](../CONTRIBUTING.md).
