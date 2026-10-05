# Installing and updating cg

cg is a Python package (Python 3.11+). The PyPI name is `cg-code-graph`; the commands are `cg` and `cg-mcp`. It installs as a user-level tool. No checkout and no sudo.

## Install

| | command |
|---|---|
| PyPI (recommended) | `pipx install cg-code-graph` or `uv tool install cg-code-graph` |
| macOS / Linux | `curl -fsSL https://raw.githubusercontent.com/cyberchronos00/code-graph/main/install.sh \| sh` |
| Windows (PowerShell) | `irm https://raw.githubusercontent.com/cyberchronos00/code-graph/main/install.ps1 \| iex` |

In a virtual environment, `pip install cg-code-graph` also works. Latest commit: `uv tool install git+https://github.com/cyberchronos00/code-graph` or `pipx install git+https://github.com/cyberchronos00/code-graph`. A tag: `uv tool install git+https://github.com/cyberchronos00/code-graph@vX.Y.Z`, or `install.sh --version vX.Y.Z`.

Then run `cg doctor`. It lists the tools it found, whether extractor dependencies are installed, and for each language whether `cg index` is exact or heuristic, why, and what to install. `cg doctor <project>` checks that project. The MCP `doctor` tool prints the same report. See `cg doctor -h`.

## Update and uninstall

| | update | uninstall |
|---|---|---|
| uv | `uv tool upgrade cg-code-graph` | `uv tool uninstall cg-code-graph` |
| pipx | `pipx upgrade cg-code-graph` | `pipx uninstall cg-code-graph` |
| script | `install.sh --update` (`install.ps1 -Update`) | `install.sh --uninstall` (`install.ps1 -Uninstall`) |

The old package name `codegraph` (through v0.9.0) is removed by the install scripts. By hand: uninstall `codegraph`, then install `cg-code-graph`.

## Elsewhere

- Extractors install on the first index that needs them, or with `cg setup`. Cache and prune: `cg setup -h` and [cli.md](cli.md#clean).
- Exact mode for Rust, C / C++, Kotlin and Swift needs optional indexers: [native.md](native.md), [kotlin.md](kotlin.md), [swift.md](swift.md).
- What the install scripts do: `install.sh --help`. Working on a checkout of cg: [CONTRIBUTING.md](../CONTRIBUTING.md).
