#!/bin/sh
# cg installer for macOS / Linux: installs (or updates / removes) the `cg` command with uv or pipx, sets up the
# Node / PHP / Dart extractor dependencies in the user cache, and optionally the exact-mode indexers.
# Never uses sudo; every step that installs something prints what it does first. Safe to run again.
#
#   curl -fsSL https://raw.githubusercontent.com/cyberchronos00/code-graph/main/install.sh | sh
#   sh install.sh [--update] [--version vX.Y.Z] [--source PATH|URL] [--with rust,c,kotlin,swift]
#                 [--no-extractors] [--uninstall] [--dry-run]
set -eu

REPO_URL="https://github.com/cyberchronos00/code-graph"
PKG="codegraph"
MIN_PY="3.11"
ACTION="install"; VERSION=""; SOURCE=""; WITH=""; EXTRACTORS=1; DRY=0
BIN_DIR="${XDG_BIN_HOME:-$HOME/.local/bin}"
ORIG_PATH="$PATH"
# where uv, pipx, rustup and the tools they install put their commands
PATH="$BIN_DIR:$HOME/.local/bin:$HOME/.cargo/bin:$PATH"; export PATH

say() { printf '%s\n' "cg-install: $*" >&2; }
run() { say "+ $*"; [ "$DRY" = 1 ] || "$@"; }
die() { printf '%s\n' "cg-install: error: $*" >&2; exit 1; }

while [ $# -gt 0 ]; do
  case "$1" in
    --update|--upgrade) ACTION="update" ;;
    --uninstall) ACTION="uninstall" ;;
    --version) [ $# -ge 2 ] || die "--version needs a tag (vX.Y.Z)"; VERSION="$2"; shift ;;
    --version=*) VERSION="${1#*=}" ;;
    --source) [ $# -ge 2 ] || die "--source needs a path or URL"; SOURCE="$2"; shift ;;
    --source=*) SOURCE="${1#*=}" ;;
    --with) [ $# -ge 2 ] || die "--with needs a list (rust,c,kotlin,swift)"; WITH="$2"; shift ;;
    --with=*) WITH="${1#*=}" ;;
    --no-extractors) EXTRACTORS=0 ;;
    --dry-run) DRY=1 ;;
    -h|--help) sed -n '2,8p' "$0" 2>/dev/null | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) die "unknown option $1 (see --help)" ;;
  esac
  shift
done

# --- what to install: git URL (optionally at a tag), or a local checkout / other URL
spec() {
  if [ -n "$SOURCE" ]; then
    case "$SOURCE" in
      git+*|http*://*|*.whl|*.tar.gz) s="$SOURCE" ;;
      *) [ -e "$SOURCE/pyproject.toml" ] || die "--source $SOURCE: no pyproject.toml there"
         s="$(cd "$SOURCE" && pwd)" ;;
    esac
  else
    s="git+$REPO_URL"
  fi
  if [ -n "$VERSION" ]; then
    case "$s" in git+*) s="$s@$VERSION" ;; *) die "--version only applies to the git source" ;; esac
  fi
  printf '%s' "$s"
}

py_ok() {  # $1: interpreter; true if >= MIN_PY
  "$1" -c "import sys; sys.exit(0 if sys.version_info >= tuple(map(int, '$MIN_PY'.split('.'))) else 1)" 2>/dev/null
}
find_python() {
  for p in python3.14 python3.13 python3.12 python3.11 python3 python; do
    if command -v "$p" >/dev/null 2>&1 && py_ok "$p"; then command -v "$p"; return 0; fi
  done
  return 1
}

find_installer() {
  if command -v uv >/dev/null 2>&1; then echo uv; return; fi
  if command -v pipx >/dev/null 2>&1; then echo pipx; return; fi
  echo none
}

installed_with() {
  if command -v uv >/dev/null 2>&1 && uv tool list 2>/dev/null | grep -q "^$PKG "; then echo uv; return; fi
  if command -v pipx >/dev/null 2>&1 && pipx list --short 2>/dev/null | grep -q "^$PKG "; then echo pipx; return; fi
  echo none
}

cache_dir() { printf '%s' "${CODEGRAPH_CACHE:-${XDG_CACHE_HOME:-$HOME/.cache}/codegraph}"; }

uninstall() {
  w="$(installed_with)"
  case "$w" in
    uv) run uv tool uninstall "$PKG" ;;
    pipx) run pipx uninstall "$PKG" ;;
    *) say "cg is not installed with uv or pipx (nothing to uninstall)" ;;
  esac
  d="$(cache_dir)/extractors"
  if [ -d "$d" ]; then say "removing the extractor dependencies in $d"; run rm -rf "$d"; fi
  say "left in place: index caches in $(cache_dir) (delete the directory to free the space), uv / pipx themselves"
}

INSTALLER=""
ensure_installer() {
  INSTALLER="$(find_installer)"
  if [ "$INSTALLER" = none ]; then
    say "neither uv nor pipx found: installing uv for this user (https://astral.sh/uv, into ~/.local/bin, no sudo)"
    command -v curl >/dev/null 2>&1 || die "curl is needed to install uv (or install pipx / uv yourself and rerun)"
    if [ "$DRY" = 1 ]; then say "+ curl -LsSf https://astral.sh/uv/install.sh | sh"; else curl -LsSf https://astral.sh/uv/install.sh | sh >&2; fi
    PATH="$HOME/.local/bin:$PATH"; export PATH
    INSTALLER=uv
  fi
}

install_cg() {
  ensure_installer; i="$INSTALLER"
  s="$(spec)"
  py="$(find_python || true)"
  if [ "$i" = uv ]; then
    if [ -n "$py" ]; then pyarg="$py"; else
      say "no Python >= $MIN_PY on PATH: uv downloads a managed Python for cg's environment"; pyarg=">=$MIN_PY"
    fi
    if [ "$ACTION" = update ] && [ -z "$SOURCE" ] && [ -z "$VERSION" ] && [ "$(installed_with)" = uv ]; then
      run uv tool upgrade "$PKG"
    else
      run uv tool install --force --reinstall --python "$pyarg" "$s"
    fi
    run uv tool update-shell >/dev/null 2>&1 || true
  else
    [ -n "$py" ] || die "Python >= $MIN_PY not found (install it, or install uv: https://docs.astral.sh/uv/)"
    # pipx upgrade only reinstalls a git install when its version changed: --update reinstalls to get the latest commit
    run pipx install --force --python "$py" "$s"
  fi
}

cg_bin() {
  if command -v cg >/dev/null 2>&1; then command -v cg; return; fi
  for d in "$BIN_DIR" "$HOME/.local/bin"; do [ -x "$d/cg" ] && { echo "$d/cg"; return; }; done
  echo cg
}

with_rust() {
  if command -v rust-analyzer >/dev/null 2>&1 || [ -x "$HOME/.cargo/bin/rust-analyzer" ]; then say "rust: rust-analyzer already installed"; return; fi
  if command -v rustup >/dev/null 2>&1 || [ -x "$HOME/.cargo/bin/rustup" ]; then
    r="$(command -v rustup || echo "$HOME/.cargo/bin/rustup")"
    say "rust: adding the rust-analyzer component with rustup"; run "$r" component add rust-analyzer
  else
    say "rust: rustup not found. Install Rust for this user (no sudo): curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh"
    say "      then: rustup component add rust-analyzer   (and rerun with --with rust)"
  fi
}

with_c() {
  if command -v scip-clang >/dev/null 2>&1 || [ -x "$HOME/.local/bin/scip-clang" ]; then say "c: scip-clang already installed"; return; fi
  case "$(uname -s)-$(uname -m)" in
    Linux-x86_64) asset=scip-clang-x86_64-linux ;;
    Darwin-arm64) asset=scip-clang-arm64-darwin ;;
    *) say "c: no scip-clang release binary for $(uname -s) $(uname -m); C / C++ stays heuristic (docs/native.md)"; return ;;
  esac
  url="https://github.com/sourcegraph/scip-clang/releases/download/v0.4.0/$asset"
  say "c: downloading scip-clang v0.4.0 into $HOME/.local/bin ($url)"
  run mkdir -p "$HOME/.local/bin"
  run curl -fL -o "$HOME/.local/bin/scip-clang" "$url"
  run chmod +x "$HOME/.local/bin/scip-clang"
  say "c: exact mode also needs a compile_commands.json in the project (cmake -DCMAKE_EXPORT_COMPILE_COMMANDS=ON, bear -- make)"
}

with_kotlin() {
  if ! command -v java >/dev/null 2>&1 && [ -z "${JAVA_HOME:-}" ]; then
    say "kotlin: no JDK found. Install JDK 17+ (no sudo: https://adoptium.net archive into ~/tools, or sdk install java 17-tem;"
    say "        with your package manager this needs sudo, e.g. sudo apt install openjdk-17-jdk-headless)"
  fi
  if command -v scip-java >/dev/null 2>&1 || [ -x "$HOME/.local/bin/scip-java" ]; then say "kotlin: scip-java already installed"
  elif command -v cs >/dev/null 2>&1; then say "kotlin: installing scip-java with coursier"; run cs install scip-java
  else
    say "kotlin: scip-java not found. Install coursier (https://get-coursier.io, no sudo), then: cs install scip-java"
  fi
  say "kotlin: exact mode runs the project's Gradle / Maven build; opt in per run with CODEGRAPH_KOTLIN_SCIP=1"
}

with_swift() {
  if command -v swift >/dev/null 2>&1; then say "swift: $(swift --version 2>&1 | head -1)"
  elif [ "$(uname -s)" = Darwin ]; then say "swift: install the Xcode command line tools: xcode-select --install"
  else say "swift: install a Swift toolchain for this user with swiftly (https://www.swift.org/install/linux/, no sudo for the toolchain itself)"
  fi
  say "swift: exact mode builds the SwiftPM package with an index store; opt in with CODEGRAPH_SWIFT_INDEX=1"
}

# --- main
case "$ACTION" in
  uninstall) uninstall; exit 0 ;;
esac
say "installing with $(find_installer | sed 's/none/uv (to be installed)/') from $(spec)"
install_cg
CG="$(cg_bin)"
if [ "$EXTRACTORS" = 1 ]; then
  say "setting up extractor dependencies (TypeScript: npm, PHP: Composer, Dart: pub) for the toolchains present"
  run "$CG" setup || say "some extractor dependencies were not installed; \`cg doctor\` says which and how"
fi
for w in $(printf '%s' "$WITH" | tr ',' ' '); do
  case "$w" in
    rust) with_rust ;; c|cpp|c_cpp) with_c ;; kotlin|java) with_kotlin ;; swift) with_swift ;;
    "") ;; *) say "--with: unknown '$w' (rust, c, kotlin, swift)" ;;
  esac
done
CG_DIR="$(dirname "$CG")"; [ "$CG_DIR" = . ] && CG_DIR="$BIN_DIR"
case ":$ORIG_PATH:" in *":$CG_DIR:"*) ;; *) say "open a new shell or add $CG_DIR to PATH to use cg (uv tool update-shell / pipx ensurepath)";; esac
[ "$DRY" = 1 ] || "$CG" doctor || true
say "done. Update later with: sh install.sh --update   (or: uv tool upgrade $PKG / pipx upgrade $PKG)"
