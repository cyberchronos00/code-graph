# Rust, C and C++

code-graph indexes Rust, C and C++ natively. Each language has two modes:

| mode | needs | what you get |
|---|---|---|
| **exact** (SCIP) | Rust: `rust-analyzer`. C/C++: `scip-clang` plus a `compile_commands.json` | Every definition and reference is resolved by the compiler front end. Edges are `exact`, or `resolved` when they go through a receiver (`x.m()`, `p->f`) or dispatch |
| **heuristic** (fallback) | only the tree-sitter Python packages | Name-based resolution over a syntax tree. Every resolved reference is labelled `heuristic` |

Both modes produce the **same node ids**: the syntactic layer (tree-sitter) defines the nodes, and the SCIP index adds
or upgrades the edges. A graph built in heuristic mode can therefore be compared with an exact one later.

The syntactic layer also adds the facts compilers don't report: crates and modules, entry points, `pub` API
surface, `#[cfg]` / `#if` gates, environment variable reads, `unsafe`, FFI, and routes.

## Install

```bash
.venv/bin/pip install tree-sitter tree-sitter-rust tree-sitter-c tree-sitter-cpp   # required for both modes

# Rust, exact mode
rustup component add rust-analyzer          # or: https://rust-analyzer.github.io/ (any release since 2024)
rust-analyzer --version

# C / C++, exact mode: scip-clang (single static binary, Linux x86_64 / macOS arm64)
curl -fL -o ~/.local/bin/scip-clang \
  https://github.com/sourcegraph/scip-clang/releases/download/v0.4.0/scip-clang-x86_64-linux   # or ...-arm64-darwin
chmod +x ~/.local/bin/scip-clang && scip-clang --version
```

The tools are looked up in `PATH`, then `~/.cargo/bin` (rust-analyzer) and `~/.local/bin` (scip-clang). Override the
lookup with `CODEGRAPH_RUST_ANALYZER=/path` or `CODEGRAPH_SCIP_CLANG=/path`. SCIP output is cached under
`~/.cache/codegraph/scip/`, keyed by a fingerprint of the sources and the indexer version, so a re-index with no changes
takes seconds.

## Rust

**Detection:** a `Cargo.toml` at the root. Workspaces, packages and targets come from
`cargo metadata --no-deps --offline`; without cargo, the manifests are read directly.

**Exact mode** runs `rust-analyzer scip <root>`. Build scripts and proc-macros are off by default (safe: nothing
from the project is executed). `CODEGRAPH_RUST_BUILD_SCRIPTS=1` turns both on, for crates whose items come from
`build.rs` or derive macros. Cargo features are `all`.

**Node keys** are Rust paths:
- `crate::module::item`
- `crate::Type::method` for inherent methods
- `crate::module::<Type as Trait>::method` for trait impls

Non-library targets are qualified by target: `kv_core[test:roundtrip]::put_then_get`, `kv_core[bench:put]::main`,
`kv_core[example:basic]::main`, `kv_core[build]::main`. The binary crate is named after the bin target (`kv::main`).

| fact | how it shows up |
|---|---|
| crates, modules | `crate:<name>` and `mod:<path>` nodes, CONTAINS edges; `--group by` module in `reaches` output |
| traits, impls | IMPLEMENTS (type → trait), IMPLEMENTED_BY (trait method → impl method; `resolved` in exact mode, `heuristic` otherwise). Calls on `dyn Trait` or generic `T: Trait` land on the trait method with `dispatch: "trait"`, and propagate to every impl. Impls of external traits (`Default`, `From`, `Display`, `Drop`, a framework's trait) are linked from their Self type (`heuristic`, `via: external_trait`), so they don't show up as dead code |
| entry points | `main` (bin targets, `#[tokio::main]`, `#[async_std::main]`, `#[actix_web::main]`), `ffi_export` (`#[no_mangle]` / `#[export_name]` functions), `test` (`#[test]`, `#[tokio::test]`, `#[rstest]`, files under `tests/`), `bench`, `example`, `build_script`, `public_api` (items reachable through `pub` from a library crate root, including `pub use` re-exports) |
| feature gates | `#[cfg(feature = "x")]` on items, statements and inner `#![cfg]` → GATED_BY → `feature:<package>/<feature>`; other cfgs → `cfg:<atom>` (e.g. `cfg:unix`, `cfg:target_os="linux"`). `cfg(test)` marks test code |
| env | `env!`, `option_env!`, `std::env::var(_os)` (also through `const` names) → READS_ENV → `env:KEY` |
| unsafe / FFI | USES_UNSAFE → `unsafe:<crate>` (unsafe blocks, `unsafe fn`, `unsafe impl`); `extern "C" { ... }` declarations are `ffi` nodes |
| routes (bonus) | axum `.route("/p", get(h).post(h2))` and actix/rocket attribute routes → `route:<METHOD> <path>` (entry `http_route`) → ROUTES_TO handler |
| attribute references | `#[serde(default = "f", with = "m", ...)]`, clap `value_parser = f` → REFERENCES_FN |
| macro-wrapped items | items inside item-level macro bodies (`cfg_rt! { pub mod rt; ... }`) are indexed when the body parses as Rust |

## C and C++

**Detection:** a `compile_commands.json` (at the root, `build*/`, `out/`, `cmake-build-*/`, or `$CODEGRAPH_COMPDB`),
or C/C++ sources in a repo that is not a Rust, Node, PHP, Go or Python project. `CODEGRAPH_CFAMILY=1` forces the
plugin on and `=0` turns it off.

**Exact mode** runs scip-clang over a compile database (`compile_commands.json`), which records how each file is
compiled: include paths, defines and language standard. Generate it with the build system:

| build system | command (in the project root) |
|---|---|
| CMake | `cmake -S . -B build -DCMAKE_EXPORT_COMPILE_COMMANDS=ON` (the configure step is enough for most projects) |
| Meson | `meson setup build` (writes `build/compile_commands.json`) |
| Make, autotools, others | `bear -- make -j8` (`bear` is in most package managers; this runs a real build) |
| Bazel | [hedronvision/bazel-compile-commands-extractor](https://github.com/hedronvision/bazel-compile-commands-extractor) |
| Xcode, MSBuild | export from the IDE, or use the project's CMake build if it has one |

The database is found at the root, in `build*/`, `out/` or `cmake-build-*/`, or at `CODEGRAPH_COMPDB`. Generated
headers (`config.h` and similar) come from the configure step, so run it first; files listed in the database but never
generated (unity sources) are skipped. Without a database, or without scip-clang, the plugin indexes in heuristic mode
and the index stats show `"mode": "heuristic"` with the command that generates the database.

```bash
cg index path/to/project --db out/project.db > out/project.stats.json
grep -E '"mode"|"nodes"|"edges"|"seconds"' out/project.stats.json      # "mode": "scip" = exact

cg reaches rb_create --db out/project.db              # every dependent, grouped RUNTIME / LIBRARY / DEV
cg impact Parser::parse --db out/project.db           # callers up to entry points (main, exported API, tests)
cg path main write_block --db out/project.db          # one call chain with file:line for every hop
cg reaches define:USE_SSL --db out/project.db         # code under #ifdef USE_SSL, and who reaches it
cg reaches env:APP_DEBUG --db out/project.db          # who reads this environment variable
cg reaches src/net/socket.c --db out/project.db       # every dependent of anything in a file
cg downstream main --db out/project.db                # env keys and #if gates a program touches
```

Confidence in exact mode: `exact` means clang reported the reference; `resolved` means it went through `.` / `->`, a
macro expansion (`via_macro`) or virtual dispatch. Calls to a virtual method land on the base method and fan out
through OVERRIDDEN_BY / IMPLEMENTED_BY to every override, so all overrides are covered. The index reflects one
configuration (the compile database's defines): code in inactive `#if` branches is still a node from the syntax
layer, and a gates file with `defines_off` answers "what if FOO were off" (see [gate scenarios](#gate-scenarios)).
`CODEGRAPH_NO_CACHE=1` forces a fresh scip-clang run; on a fresh run the stats include scip-clang's command, its
last stderr lines and its summary (`num errored TUs`).

**Unreferenced functions** (no entry point reaches them) are a starting point for dead-code review:

```bash
.venv/bin/python - <<'PY'
import sqlite3; c = sqlite3.connect("out/project.db")
for r in c.execute("""SELECT id, file, line FROM nodes n WHERE kind IN ('function','method')
                      AND entry_kind IS NULL AND NOT EXISTS (SELECT 1 FROM node_entry e WHERE e.node_id = n.id)
                      ORDER BY file, line"""): print(*r)
PY
```

Confirm each one first: functions stored as pointers at runtime (exact mode records the pointer-taking site as
REFERENCES_FN), callbacks registered with a library, and code in `#if` branches that were off in the compile database
can be live.

**Node keys:**
- functions and methods use their qualified name (`ns::Class::method`, `rb_create`);
- file-local items get the file prefix (`src/ringbuf.c#rb_lock` for `static`, anonymous namespaces);
- overloads get the parameter types (`format(format_string<T...>)`);
- same-named functions in different programs get the file prefix (`tools/a.c#main`).

| fact | how it shows up |
|---|---|
| translation units, headers | `file:<path>` nodes (`translation_unit`, `header` attrs); INCLUDES edges resolved through the including directory, the compile database's `-I` paths, `include/`, then a unique basename (`heuristic`) |
| entry points | `main` / `wmain` / `WinMain` (`test`, `example` or `bench` by directory), `test` (gtest/Catch2/doctest/Boost.Test/check `TEST*` macros, `test*` functions in test directories), `bench` (`BENCHMARK`), `public_api` (functions declared with an export macro such as `FOO_API` / `__declspec(dllexport)` / visibility default, or declared in an `include/` header, and not `static`/private) |
| virtual dispatch | exact mode: scip-clang's override relationships → OVERRIDDEN_BY (base → override) and IMPLEMENTED_BY (pure virtual → implementation), `resolved`; heuristic mode: same-named methods of derived classes, `heuristic`. Calls to virtual methods carry `dispatch: "virtual"` |
| preprocessor gates | `#if` / `#ifdef` / `#elif` / `#else` regions (include guards and `__cplusplus` excluded) → GATED_BY → `define:<MACRO>` |
| env | `getenv`, `secure_getenv`, `std::getenv`, `_wgetenv` with a literal key → READS_ENV |
| macros | function-like macros are `macro` nodes and CALLS targets. References that clang reports at a macro expansion site are kept as `resolved` with `via_macro`; expansions with more than 8 symbols at one site (type-check macros) are dropped |

**Directories skipped** by default: `third_party` (and spellings), `vendor`, `external`, `extern`, `deps`, `_deps`,
`build*`, `cmake-build*`, `CMakeFiles`, `bazel-out`, `googletest`/`gtest`/`gmock`/`catch2`/`doctest`, `node_modules`,
dot-directories. To change the list, use
`CODEGRAPH_EXCLUDE_DIRS=a,b` or `CODEGRAPH_INCLUDE_DIRS=deps`.

## Entry kinds and `reaches` groups

| group | entry kinds |
|---|---|
| RUNTIME | `main`, `ffi_export` (plus `http_route` etc. as before) |
| LIBRARY API | `public_api`: reached only through a library's exported surface |
| DEV/BUILD-ONLY | `test`, `bench`, `example`, `build_script` |

`reaches` and `impact` group the results by module: the Rust module path, or the directory for C/C++.

## Query specs

| spec | selects |
|---|---|
| `kv_core::store::Store::get` | a function, method or type by qualified path (suffix match: `Store::get`, `store::Store::get`) |
| `MemoryStore::get` | also matches the trait impl `<MemoryStore as Store>::get` |
| `bus::Handler` | a type and its methods |
| `rb_create` | a bare function name (then type names) |
| `src/ringbuf.c`, `crates/kv-core/src/util.rs` | every item defined in a file |
| `kv_core::store`, `mod:kv_core::store` | every function in a Rust module |
| `env:KV_DATA_DIR`, `unsafe:kv_core`, `feature:kv-core/fs`, `cfg:unix`, `define:RB_THREADSAFE` | fact nodes |

## Gate scenarios

The same `--gates` file format, with native keys (see [`examples/native.gates.json`](../examples/native.gates.json)):

```json
{"scenarios": [{"name": "minimal_build",
  "features_off": ["kv-core/fs"], "features_on": [], "cargo_features": "default",
  "cfg_true": ["unix"], "cfg_false": ["windows"],
  "defines_on": ["NDEBUG", "LEVEL=2"], "defines_off": ["RB_THREADSAFE"]}]}
```

Edges inside a `#[cfg]` item or statement, or inside a `#if` region, that is false under the scenario get the
scenario as their gate. `reaches` then reports them under GATED. Unknown atoms stay live (conservative).

## Environment variables

| variable | effect |
|---|---|
| `CODEGRAPH_RUST_SCIP=0` / `CODEGRAPH_C_SCIP=0` | force heuristic mode |
| `CODEGRAPH_RUST_ANALYZER`, `CODEGRAPH_SCIP_CLANG` | indexer binary |
| `CODEGRAPH_RUST_SCIP_FILE`, `CODEGRAPH_C_SCIP_FILE` | use an existing `index.scip` instead of running the indexer |
| `CODEGRAPH_RUST_BUILD_SCRIPTS=1` | let rust-analyzer run build scripts and proc-macros |
| `CODEGRAPH_COMPDB` | path to `compile_commands.json` (or its directory) |
| `CODEGRAPH_CFAMILY=0/1` | disable / force the C/C++ plugin |
| `CODEGRAPH_JOBS` | scip-clang worker count |
| `CODEGRAPH_INDEXER_TIMEOUT` | seconds (default 3600) |
| `CODEGRAPH_NO_CACHE=1`, `CODEGRAPH_CACHE_DIR` | SCIP cache control |
| `CODEGRAPH_NO_CARGO=1`, `CODEGRAPH_CARGO` | skip `cargo metadata` / cargo binary |
| `CODEGRAPH_EXCLUDE_DIRS`, `CODEGRAPH_INCLUDE_DIRS` | C/C++ directory filters (comma-separated names) |
| `CODEGRAPH_C_MASK_ANNOTATIONS=0` | do not blank annotation macros (`FOO_API`, `FOO_CONSTEXPR`) before parsing |
| `CODEGRAPH_C_MAX_MACRO_REFS` | max references kept per macro expansion site (default 8) |
| `CODEGRAPH_MAX_FILE_BYTES` | skip larger C/C++ files (default 30 MB; amalgamations) |

## Validation on public projects

These numbers come from shallow clones (autumn 2026) on a Linux x86_64 machine, cold cache, exact mode. Precision was spot-checked on
random samples of 20 CALLS / REFERENCES_FN / USES_TYPE / IMPLEMENTED_BY edges per project (two samples each). Every
sampled edge was correct. The ones that look odd on the line are references through a macro expansion, an `.await`
(→ `Future::poll`) or an operator. "Heuristic vs exact" compares the fallback mode's call edges with exact mode's,
on the same files.

| project | lang | files | index time (indexer) | nodes / edges | heuristic precision / recall vs exact |
|---|---|---|---|---|---|
| ripgrep | Rust | 109 | 18 s (14 s) | 5.5k / 25.8k | 90% / 56% |
| tokio | Rust | 795 | 73 s (65 s) | 13.6k / 70.4k | 71% / 47% |
| fmt | C++ | 70 | 17 s (15 s) | 5.3k / 24.1k | 68% / 31% |
| leveldb | C++ | 133 | 3.7 s (2.8 s) | 3.1k / 12.3k | 87% / 50% |
| curl | C | 1,050 | 19 s (7 s) | 19.2k / 106.6k | 86% / 83% |
| redis | C | 281 | 12 s (3 s) | 16.2k / 156.1k | 90% / 82% |

In short, heuristic mode is good enough for orienting in plain C. For Rust and modern C++ (generics, overloads,
templates, macros) use exact mode.
