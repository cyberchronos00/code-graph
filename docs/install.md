# Installing and updating cg

cg is a Python package (Python 3.11+) with two commands, `cg` (CLI) and `cg-mcp` (MCP server). It installs as a
user-level tool in its own environment; no checkout and no sudo are needed.

## Install

| | command |
|---|---|
| macOS / Linux script | `curl -fsSL https://raw.githubusercontent.com/cyberchronos00/code-graph/main/install.sh \| sh` |
| Windows script (PowerShell) | `irm https://raw.githubusercontent.com/cyberchronos00/code-graph/main/install.ps1 \| iex` |
| uv | `uv tool install git+https://github.com/cyberchronos00/code-graph` |
| pipx | `pipx install git+https://github.com/cyberchronos00/code-graph` |
| a release | `uv tool install git+https://github.com/cyberchronos00/code-graph@vX.Y.Z` (or `install.sh --version vX.Y.Z`) |

Then run `cg doctor`: it lists the tools found, whether the extractor dependencies are installed, and per language
whether `cg index` runs in exact or heuristic mode, why, and the command that installs what is missing.
`cg doctor <project>` checks one project (only its languages, its `compile_commands.json`, its `.cg.yaml`). The MCP
server has the same report as the `doctor` tool.

## Update and uninstall

| | update | uninstall |
|---|---|---|
| uv | `uv tool upgrade codegraph` | `uv tool uninstall codegraph` |
| pipx | `pipx upgrade codegraph` | `pipx uninstall codegraph` |
| script | `install.sh --update` (`install.ps1 -Update`) | `install.sh --uninstall` (`install.ps1 -Uninstall`) |

`--update` upgrades with whichever of uv / pipx installed cg; with `--version vX.Y.Z` it installs that tag instead
(tags after v0.6.0: earlier ones have no `pyproject.toml`).
`--uninstall` also removes the extractor dependencies from the cache; the index caches stay (the directory is
printed).

## What the scripts do

`install.sh` (macOS / Linux, POSIX sh) and `install.ps1` (Windows) print every command before running it, never use
sudo / elevation, and can be run again (an install over an existing one replaces it):

1. find Python 3.11+; use uv if installed, else pipx, else install uv for the user (`~/.local/bin`, from
   astral.sh). Without a suitable Python, uv downloads a managed one for cg's environment;
2. install cg (`uv tool install` / `pipx install`) from GitHub, a tag (`--version`), or `--source PATH|URL` (a local
   checkout, another branch: `--source git+https://github.com/cyberchronos00/code-graph@my-branch`);
3. run `cg setup`: the Node / PHP / Dart extractor dependencies for the toolchains present (skip with
   `--no-extractors`);
4. `--with rust,c,kotlin,swift`: the optional exact-mode indexers. `rust` adds rust-analyzer with rustup; `c`
   downloads the scip-clang release binary into `~/.local/bin` (Linux x86_64, macOS arm64); `kotlin` installs scip-java
   with coursier when `cs` is there; `swift` and anything that would need sudo (a JDK or toolchain from the system
   package manager) is printed as instructions instead;
5. run `cg doctor`.

`install.sh --dry-run` prints the steps without running them. `install.ps1` has not been run on Windows yet; report
problems as issues.

## Extractor dependencies

The TypeScript, PHP and Dart extractors ship as sources inside the package; their dependencies do not:

| language | toolchain | dependencies |
|---|---|---|
| TypeScript / JavaScript | Node.js 20+, npm | `npm ci` (typescript, @vue/compiler-sfc) |
| PHP | PHP 8.2+, Composer 2 | `composer install` (nikic/php-parser) |
| Dart | Dart SDK 3.x | `dart pub get` + `dart compile exe` |

They install on the first index that needs them, or ahead of time with `cg setup [typescript php dart]`, into
`$CODEGRAPH_CACHE/extractors/<language>-<lock hash>` (default `~/.cache/codegraph`, `%LOCALAPPDATA%\codegraph` on
Windows). The directory is keyed by the lock file: an update that changes only the extractor code reuses the
installed dependencies, one that changes the lock file installs fresh ones. A development checkout whose extractor
directory already has its dependencies (`npm ci` run there) keeps using it.

## Exact-mode indexers

Python, TypeScript, PHP and Dart are always parsed exactly. Rust, C / C++, Kotlin and Swift run on a tree-sitter
layer (heuristic, name-based calls) unless their compiler indexer is available: rust-analyzer, scip-clang plus a
`compile_commands.json`, scip-java plus a JDK (opt in with `CODEGRAPH_KOTLIN_SCIP=1`, since it runs the build), a
Swift toolchain (opt in with `CODEGRAPH_SWIFT_INDEX=1`). See [native.md](native.md), [kotlin.md](kotlin.md) and
[swift.md](swift.md).

In Rust exact mode, rust-analyzer runs once more for each other target the `cfg` conditions name (up to 3), which
about doubles a cold index. `rust: {targets: off}` in `.cg.yaml` or `CODEGRAPH_RUST_TARGETS=0` turns that off;
`cg doctor <project>` shows the setting in effect and where it comes from.

## From a checkout

For working on cg itself, see [CONTRIBUTING.md](../CONTRIBUTING.md): `pip install -e ".[dev]"` in a virtual
environment, extractor dependencies in the checkout.
