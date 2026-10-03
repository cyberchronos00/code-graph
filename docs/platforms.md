# Platform-specific code

Apps and libraries that ship to several targets keep per-platform code side by side: `#[cfg(windows)]` functions,
`#ifdef _WIN32` branches, `storage.ios.ts` next to `storage.android.ts`, a Dart library imported only
`if (dart.library.js_interop)`. cg tags that code with the targets it is built for, so you can ask what a change
does on one target (`--platform ios`) and which targets a set of variants leaves uncovered.

```text
$ cg impact open_logs --db out/app.db --platform windows
platform: windows (4 nodes and 13 references not built for it left out; 0 conditions could not be evaluated for it, the code under them stays in)
not built for windows: function:dirs_demo::open_logs
open_logs is not built for windows: nothing calls it there (`cg platforms divergence --target windows` lists references to it that would not build)

$ cg platforms divergence --db out/app.db
== VARIANTS (implemented per platform): 1, 0 with a declared target no variant covers
  dirs_demo::paths::config_dir  [per-platform definitions]  covered: windows, linux, macos
      src/paths.rs:4  windows  (cfg(target_os = "windows"))
      src/paths.rs:9  linux, macos, ios, android  (cfg(unix))
      used at: src/main.rs:8

== REFERENCED WHERE THE CALLEE IS NOT BUILT: 1
  function:dirs_demo::main -CALLS-> function:dirs_demo::open_logs  @ src/main.rs:18  missing on: windows, macos  (callee: linux, cfg(target_os = "linux"))
```

## Targets

The known targets are `windows`, `linux`, `macos`, `ios`, `android` and `web`; `--platform` also takes the usual
aliases (`win32`, `darwin`, `osx`, `mac`, `wasm`, `browser`, ...). A project's own targets come from, in order:

| source | targets |
|---|---|
| `.cg.yaml` `platforms.targets` | exactly the listed ones |
| Flutter: platform folders next to a Flutter `pubspec.yaml` (`android/`, `ios/`, `web/`, `macos/`, `windows/`, `linux/`) | one per folder |
| Expo: `app.json` `expo.platforms` | the listed ones (Expo's default: ios, android) |
| React Native (`react-native` / `expo` dependency) | ios, android, plus web / windows / macos with `react-native-web` / `-windows` / `-macos` |
| SwiftPM: `platforms: [.iOS(.v16), .macOS(.v13)]` in `Package.swift` (root or one level down) | the listed ones (tvOS / watchOS / visionOS count as ios, macCatalyst as macos) |
| Kotlin Multiplatform: the targets in the `kotlin { }` block of a multiplatform `build.gradle(.kts)` (root, one or two levels down) | `androidTarget()` / `androidNative*()` android, `iosArm64()` / `iosX64()` / `tvos*` / `watchos*` ios, `macosArm64()` macos, `linuxX64()` linux, `mingwX64()` windows, `js()` / `wasmJs()` web, `jvm("desktop")` / Compose Desktop windows, linux, macos |
| Tauri 2 mobile (`src-tauri/gen/android/`, `src-tauri/gen/apple/`) | windows, linux, macos plus android / ios |
| Electron or Tauri (`electron` dependency, `src-tauri/tauri.conf.json`) | windows, linux, macos |
| anything else with platform conditions | windows, linux, macos, plus a mobile or web target a condition names on its own (`target_os = "android"`, `defined(__ANDROID__)`, `Platform.OS === 'web'`) |

`cg platforms` prints the targets and where each one came from.

## What is recognised

| language | conditions | variants |
|---|---|---|
| Rust | `#[cfg(...)]` / `#![cfg(...)]` on items, `mod` declarations and statements; `if cfg!(...)` branches; `target_os`, `target_family`, `unix`, `windows`, `target_vendor`, `target_arch`, `target_env` | functions and methods defined once per `cfg` (`config_dir` + `config_dir@9`) |
| C / C++ | `#if` / `#ifdef` / `#elif` / `#else` regions on platform macros (`_WIN32`, `__APPLE__` with `TARGET_OS_IPHONE`, `__linux__`, `__ANDROID__`, `__EMSCRIPTEN__`, ...); directories `win/`, `unix/`, `posix/`, `linux/`, `darwin/`, ...; file names `*_win.c`, `*-unix.c`, `aix.c`, `os390.c` | one function per `#if` branch or per platform file; a function on one target and a function-like macro on another |
| Dart / Flutter | `Platform.isIOS` / `isAndroid` / ..., `kIsWeb`, `defaultTargetPlatform == TargetPlatform.x` and `switch` on it, `if` / `else` chains, `?:`, Dart 3 `switch` expressions | conditional imports and exports (`import 'stub.dart' if (dart.library.io) 'io.dart' if (dart.library.js_interop) 'web.dart'`) |
| TypeScript / JavaScript | React Native `Platform.OS === 'ios'`, `Platform.select({ios, android, native, web, default})`, `switch (Platform.OS)`; Node / Electron `process.platform === 'win32'`, `os.platform()` | `.ios.ts` / `.android.ts` / `.native.ts` / `.web.ts` files and the base file next to them; imports resolve through the platform suffixes (`moduleSuffixes`) |

A guard clause (`if (Platform.OS !== 'ios') return`, `if (!Platform.isIOS) return;`) tags the rest of the enclosing
block with the negated condition, and a JS / TS branch without braces or semicolons ends at its line (automatic
semicolon insertion). Swift `#if os(iOS)`, `canImport(UIKit)` (ios), `canImport(AppKit)` (macos),
`targetEnvironment(macCatalyst)` (macos) and `@available(iOS, unavailable)` / `@available(macOS, unavailable)` on a
declaration are platform conditions; `targetEnvironment(simulator)` and version-only `@available(iOS 17, *)` /
`#available` are not (see [swift.md](swift.md)). Kotlin Multiplatform source sets (`iosMain`, `androidMain`, ...) are platform conditions on
their files, and `expect` declarations link to each `actual` ([kotlin.md](kotlin.md)). Electron and Tauri process
roles (main, preload, renderer; webview, core) are recorded on module nodes by the
[bridges](bridges.md#desktop-process-boundaries-electron-and-tauri) pass. Native files that receive [web / native bridge](bridges.md) calls are tagged with the platform of
their folder (`android/`, `ios/`, `macos/`), and a Capacitor project's targets come from `capacitor.config.*` with
`android/` / `ios/` next to it, plus web.

Each condition is evaluated per target to true, false or unknown. A condition that does not decide the target
(`feature = "x"`, `HAVE_SOUND`, `Platform.Version > 30`) counts as unknown, and the code under it stays in every
target's view. `cg platforms` and `cg coverage` list how many conditions are unknown, with samples.

## In the graph

- `nodes.attrs.platforms`: the targets a symbol is built for (absent: every target). `platform_expr` is the source
  condition (`cfg(unix)`, `#if defined(_WIN32)`, `.ios file`, `if (dart.library.io) (lib/sync.dart:1)`),
  `platform_at` its file:line and `platform_unknown` the targets on which it could not be evaluated.
- `edges.attrs.platforms`: the same for a call or reference inside a platform branch (`if (Platform.OS === 'ios')
  { openIosSettings() }`).
- Variant links: a call that resolves to one variant (`storage.ios.ts`, the stub of a conditional import, the
  host's `cfg` in rust-analyzer, a Swift function defined once per `#if os(...)` branch) is also linked to its sibling variants, with `attrs.platform_variant_of`. `impact`
  on `storage.android.ts#save` finds the callers that import `./storage`.
- Rust exact mode: rust-analyzer resolves the host configuration. Calls into items gated for another target are added
  from the syntax layer (`via: cfg-inactive`), so every target's callers are in the graph.

`cg index` records a summary in the index stats (`platforms`: targets, conditions, tagged symbols and references,
per-target counts, divergence findings, the pass's seconds).

## Filtering queries: `--platform`

`reaches`, `impact`, `downstream`, `path`, `routes` and `search` take `--platform TARGET` (MCP: `platform`). The
answer covers that target's build: symbols and references whose condition is false there are left out, and the
first line names the filter and the number of conditions that could not be evaluated for it. A target symbol that is
not built for the platform is named as such. Without `--platform` every answer is the full graph, with
platform-specific symbols labelled `[ios, android]`.

```text
$ cg impact function:lib/storage/storage_web.dart#save --db out/app.db --platform ios
platform: ios (5 nodes and 11 references not built for it left out; 0 conditions could not be evaluated for it, the code under them stays in)
not built for ios: function:lib/storage/storage_web.dart#save
function:lib/storage/storage_web.dart#save is not built for ios: nothing calls it there (...)
```

An unknown platform name exits with status 2 and lists the known targets.

## Divergence: `cg platforms divergence`

| finding | meaning |
|---|---|
| VARIANTS | a symbol or module implemented per platform, with the declared targets that no variant covers (a `.ios.ts` / `.android.ts` pair in an Expo app that also targets web) |
| API SURFACE DIFFERS | a variant lacks a symbol its siblings define and the importers use (`cachePath` in the io library but not in the web one) |
| REFERENCED WHERE THE CALLEE IS NOT BUILT | a call, import or type use that is live on a target where the referenced code and all its variants are absent: a build or runtime failure on that target |

`--target ios` keeps the findings that affect one target, `--kind variants|api_surface|missing_callee` one kind,
`--json` gives the structured findings. MCP: `platforms` and `platform_divergence(target, kind)`.

## Configuration

```yaml
platforms:
  targets: [ios, android, web]          # the project's targets (default: detected, see above)
  paths:                                # files built only for some targets
    "src/win32/**": [windows]
    "src/posix/**": [unix]              # also: native (every non-web target)
  file_suffixes: true                   # React Native .ios.ts / .android.ts files (default true)
  path_conventions: true                # C / C++ win/ unix/ directories and *_win.c file names (default true)
```

`cg config show` lists the effective values and where each one comes from.

## Validation

Spot checks on public projects (ripgrep, alacritty, libuv, curl, dart-lang/http, localsend, bluesky social-app) are
in [validation.md](validation.md#platform-specific-code).
