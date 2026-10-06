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

## Elsewhere

- Extractors install on the first index that needs them, or with `cg setup`. Cache and prune: `cg setup -h` and [clean](cli.md#clean).
- Exact mode for Rust, C / C++, Kotlin and Swift needs optional indexers: [Rust, C and C++](native.md), [Kotlin](kotlin.md), [Swift](swift.md).
- What the install scripts do: `install.sh --help`. Working on a checkout of cg: [Contributing](../CONTRIBUTING.md).
