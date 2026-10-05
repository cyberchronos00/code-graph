# Rust, C and C++

What Rust, C, and C++ add beyond [install.md](install.md), [CLI specs](cli.md#query-targets-specs), and [schema.md](schema.md): heuristic vs rust-analyzer / scip-clang, the indexer flags, and the ids those pages do not spell out. Toolchain fit: `cg doctor -h`. Why a file stayed heuristic: `cg coverage`.

## Modes

| mode | calls | needs |
|---|---|---|
| heuristic | name resolution, labelled `heuristic` | tree-sitter (`tree-sitter-rust`, `tree-sitter-c`, `tree-sitter-cpp`) |
| exact | compiler-resolved references (`exact`, or `resolved` through a receiver, a macro, or dispatch). Node ids stay the syntax layer's | Rust: rust-analyzer. C/C++: scip-clang plus `compile_commands.json` ([Exact mode](#exact-mode)) |

The syntax layer also records facts the compiler index does not: crates and modules, entry points, the `pub` surface, `#[cfg]` / `#if` gates, env reads, `unsafe`, FFI, and routes. Kind names: [schema.md](schema.md#node-kinds).

## Exact mode

Exact mode runs when the indexer is installed. `CODEGRAPH_RUST_SCIP=0` or `CODEGRAPH_C_SCIP=0` forces the heuristic layer. A failed run is named by `cg coverage` (`exact indexer run failed`) and the index stays on the syntax layer. SCIP output is cached under `~/.cache/codegraph/scip/` ([cli.md](cli.md#clean)).

| indexer | how |
|---|---|
| `rust-analyzer scip` | a root `Cargo.toml` (`cargo metadata`, or the manifests if cargo is missing). Build scripts and proc-macros stay off unless `CODEGRAPH_RUST_BUILD_SCRIPTS=1`. Cargo features are `all` |
| `scip-clang` | a `compile_commands.json` at the root, under `build*/`, `out/`, `cmake-build-*/`, or at `CODEGRAPH_COMPDB`. One database is one configuration |
| prebuilt index | `CODEGRAPH_RUST_SCIP_FILE` or `CODEGRAPH_C_SCIP_FILE` |

| still heuristic | why |
|---|---|
| indexer missing or `*_SCIP=0` | coverage says so |
| C/C++ with no compile database | stats `"mode": "heuristic"`. The database comes from the build system (CMake `CMAKE_EXPORT_COMPILE_COMMANDS`, Meson, `bear`) |
| inactive `#[cfg]` / `#if` | the syntax node stays. Rust re-runs rust-analyzer for other targets the `cfg`s name (`attrs.exact_target`); the rest is `via: cfg-inactive` |
| files the index omits | those files keep heuristic edges |

`CODEGRAPH_CFAMILY=1` forces the C/C++ plugin and `=0` turns it off. It steps aside in a Cargo, npm, Composer, Go, Python, or Dart repo unless a compile database exists. Generated headers come from the configure step; unity sources listed but not on disk are skipped.

## Environment variables

| variable | effect |
|---|---|
| `CODEGRAPH_RUST_SCIP=0`, `CODEGRAPH_C_SCIP=0` | force heuristic mode |
| `CODEGRAPH_RUST_ANALYZER`, `CODEGRAPH_SCIP_CLANG` | indexer binary (`PATH`, then `~/.cargo/bin` or `~/.local/bin`) |
| `CODEGRAPH_RUST_SCIP_FILE`, `CODEGRAPH_C_SCIP_FILE` | an existing `index.scip` |
| `CODEGRAPH_RUST_BUILD_SCRIPTS=1` | let rust-analyzer run build scripts and proc-macros |
| `CODEGRAPH_RUST_TARGETS` | extra rust-analyzer runs: `auto` (default, up to 3 targets the `cfg`s name), `0`, or a platform / triple list (`windows,macos`). Overrides `.cg.yaml` `rust.targets` |
| `CODEGRAPH_COMPDB` | `compile_commands.json` or its directory |
| `CODEGRAPH_CFAMILY=0` / `1` | disable or force the C/C++ plugin |
| `CODEGRAPH_JOBS` | scip-clang workers |
| `CODEGRAPH_NO_CARGO=1`, `CODEGRAPH_CARGO` | skip `cargo metadata`, or point at the cargo binary |
| `CODEGRAPH_EXCLUDE_DIRS`, `CODEGRAPH_INCLUDE_DIRS` | C/C++ directory filters (comma-separated names) |
| `CODEGRAPH_C_MASK_ANNOTATIONS=0` | do not blank annotation macros (`FOO_API`) before parsing |
| `CODEGRAPH_C_MAX_MACRO_REFS` | references kept per macro expansion site (default 8) |
| `CODEGRAPH_MAX_FILE_BYTES` | skip larger C/C++ files (default 30 MB) |
| `CODEGRAPH_NO_CACHE=1`, `CODEGRAPH_INDEXER_TIMEOUT` | fresh indexer run; cap in seconds |

## Query specs

Qualified paths, `mod:`, source files, and `feature:` / `cfg:` / `define:` / `unsafe:` / `env:` follow [cli.md](cli.md#query-targets-specs). Ids that page does not spell out:

| spec or id | selects |
|---|---|
| `kv_core[test:roundtrip]::put_then_get` | a non-library target (`test`, `bench`, `example`, `build`). The bin crate is named for its bin target (`kv::main`) |
| `crate::module::<Type as Trait>::method` | a trait impl method. A call on `dyn Trait` or `T: Trait` lands on the trait method (`dispatch: "trait"`) and reaches every impl |
| `src/ringbuf.c#rb_lock` | a file-local item (`static`, an anonymous namespace). Same-named functions in different programs take the file prefix (`tools/a.c#main`). Overloads keep parameter types in the id |

`impact` shows a virtual or trait base as `overrides:` (`via base`), not as a caller. `reaches` groups by Rust module or C/C++ directory. Gates use the same file as [configuration.md](configuration.md#gate-scenarios), with native keys `features_off`, `cfg_true`, and `defines_off`: edges under a false `#[cfg]` or `#if` are GATED; unknown atoms stay live.

## Framework facts

| area | what you can query |
|---|---|
| Rust entry | `main` (including `#[tokio::main]`, `#[async_std::main]`, `#[actix_web::main]`), `#[no_mangle]` / `#[export_name]` (`ffi_export`), `#[test]` / `#[tokio::test]` / `#[rstest]` and `tests/`, plus `bench`, `example`, `build_script`, and `pub` items from a library root (`public_api`, `pub use` included) |
| Rust routes | axum `.route("/p", get(h).post(h2))` and actix / rocket attribute routes → `route:<METHOD> <path>` (`http_route`) → `ROUTES_TO` the handler |
| Rust attrs | `#[serde(default = "f")]` and clap `value_parser = f` → `REFERENCES_FN`. A project `macro_rules!` that expands to `#[test] fn $name` is one `test` node per call (`via: "test macro body"`). Items inside an item-level macro body are indexed when that body parses as Rust |
| Impls of external traits | `Default`, `From`, `Display`, `Drop`, a framework trait: linked from the Self type (`heuristic`, `via: external_trait`) |
| C / C++ entry | `main` / `wmain` / `WinMain` (a test, example, or bench directory retags it), gtest / Catch2 / doctest / Boost.Test `TEST*` and `test*` in test dirs, `BENCHMARK`, and an export macro or a non-static declaration in `include/` (`public_api`) |
| Virtuals | exact: scip-clang → `OVERRIDDEN_BY` / `IMPLEMENTED_BY` (`resolved`). Heuristic: same-named methods on derived classes. The call carries `dispatch: "virtual"` and fans out to every override |
| Gates and env | `#[cfg(feature = "x")]` → `feature:<package>/<feature>`; other cfgs → `cfg:` (`cfg(test)` marks test code). `#if` / `#ifdef` (not an include guard or `__cplusplus`) → `define:`. `env!`, `option_env!`, `std::env::var`, `getenv` → `env:KEY`. `unsafe` blocks and `unsafe fn` / `impl` → `unsafe:<crate>`; `extern "C"` is an `ffi` node |
| Macro expansions | a function-like macro is a `macro` node and a `CALLS` target. A clang reference at an expansion is `resolved` with `via_macro`; sites with more symbols than `CODEGRAPH_C_MAX_MACRO_REFS` are dropped |
