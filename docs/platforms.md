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
      src/paths.rs:9  linux, macos  (cfg(unix))
      used at: src/main.rs:8

== REFERENCED WHERE THE CALLEE IS NOT BUILT: 1
  function:dirs_demo::main -CALLS-> function:dirs_demo::open_logs  @ src/main.rs:18  missing on: windows, macos  (callee: linux, cfg(target_os = "linux"))
```

## Targets

The known targets are `windows`, `linux`, `macos`, `ios`, `android`, `web`, `tvos`, `watchos` and `visionos`;
`--platform` also takes the usual aliases (`win32`, `darwin`, `osx`, `mac`, `wasm`, `browser`, `xros`, ...). A
project's own targets come from, in order:

| source | targets |
|---|---|
| `.cg.yaml` `platforms.targets` | exactly the listed ones |
| Flutter: platform folders next to a Flutter `pubspec.yaml` (`android/`, `ios/`, `web/`, `macos/`, `windows/`, `linux/`) | one per folder |
| Expo: `app.json` `expo.platforms` | the listed ones (Expo's default: ios, android) |
| React Native (`react-native` / `expo` dependency) | ios, android, plus web / windows / macos with `react-native-web` / `-windows` / `-macos` |
| Kotlin Multiplatform: the targets in the `kotlin { }` block of a multiplatform `build.gradle(.kts)` (root, one or two levels down; its `iosApp/` Xcode project is not read for targets) | `androidTarget()` / `androidNative*()` android, `iosArm64()` / `iosX64()` ios, `tvos*()` tvos, `watchos*()` watchos, `macosArm64()` macos, `linuxX64()` linux, `mingwX64()` windows, `js()` / `wasmJs()` web, `jvm("desktop")` / Compose Desktop windows, linux, macos |
| Xcode: the targets of every `*.xcodeproj` (root, one or two levels down): `SUPPORTED_PLATFORMS`, else `SDKROOT` (`iphoneos` ios, `macosx` macos, `appletvos` tvos, `watchos`, `xros` visionos), and `SUPPORTS_MACCATALYST = YES` (macos, as Mac Catalyst) | the platforms the targets build for (`target_sources` names the project and target) |
| SwiftPM: `platforms: [.iOS(.v16), .macOS(.v13)]` in `Package.swift` (root or one level down; the local packages' when no Xcode project or root manifest names a platform) | the listed ones (macCatalyst counts as macos); with an Xcode project, both are read |
| Tauri 2 mobile (`src-tauri/gen/android/`, `src-tauri/gen/apple/`) | windows, linux, macos plus android / ios |
| Electron or Tauri (`electron` dependency, `src-tauri/tauri.conf.json`) | windows, linux, macos |
| anything else with platform conditions | windows, linux, macos ("desktop default"), plus a mobile or web target a condition names on its own (`target_os = "android"`, `defined(__ANDROID__)`, `Platform.OS === 'web'`; `TARGET_OS_IPHONE` names ios) |

A SwiftPM package also builds where its conditions say (`#if os(Linux)` adds linux; so does a Vapor / Hummingbird
dependency). An Xcode app without a root `Package.swift` builds only for the platforms its project lists, so the
desktop default never applies to it.

### Apple platforms (#74)

- tvOS, watchOS and visionOS are their own targets: `os(visionOS)` is `visionos` and never stands for iOS. When it is
  not one of the project's targets, code under `#if os(visionOS)` is built for none of them (labelled
  `no known target`) and `#if !os(visionOS)` code for all of them.
- Mac Catalyst: when the project builds the iOS app for the Mac (`SUPPORTS_MACCATALYST = YES`) and has no AppKit
  macOS target, the macos target is the Catalyst build. There `os(iOS)` and `targetEnvironment(macCatalyst)` are true,
  `os(macOS)` is false and `canImport(UIKit)` is true. Without a Catalyst build, `targetEnvironment(macCatalyst)` is
  built for no target; with both an AppKit and a Catalyst Mac build those conditions are unknown on macos.
- Xcode target membership: a source file that only some targets compile (a widget extension without Catalyst, a
  macOS-only target), from the targets' Sources build phases and Xcode 16 synchronized folders with their
  membership exceptions, is tagged with those targets' platforms (`platform_expr: Xcode target membership
  (AppWidget)`). Files of local packages are not target members and keep their own conditions.
  `platforms.xcode_membership: false` turns this off.
- An `extension URL { }` type node is never tagged (the type exists everywhere); its members are.

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
semicolon insertion). Swift `#if os(iOS)`, `canImport(UIKit)` (ios, tvos, visionos; macos in a Catalyst app),
`canImport(AppKit)` (macos), `targetEnvironment(macCatalyst)` (macos when the app builds for Catalyst, see
[Apple platforms](#apple-platforms-74)) and `@available(iOS, unavailable)` / `@available(macOS, unavailable)` / `@available(*, unavailable)` on a
declaration are platform conditions; `targetEnvironment(simulator)` is not, and version forms (`@available(iOS 17, *)`,
`#available`) are recorded as minimum OS versions (`attrs.available`, see [swift.md](swift.md)). Kotlin Multiplatform source sets (`iosMain`, `androidMain`, ...) are platform conditions on
their files, and `expect` declarations link to each `actual` ([kotlin.md](kotlin.md)). Electron and Tauri process
roles (main, preload, renderer; webview, core) are recorded on module nodes by the
[bridges](bridges.md#desktop-process-boundaries-electron-and-tauri) pass. Native files that receive [web / native bridge](bridges.md) calls are tagged with the platform of
their folder (`android/`, `ios/`, `macos/`), and a Capacitor project's targets come from `capacitor.config.*` with
`android/` / `ios/` next to it, plus web.

Each condition is evaluated per target to true, false or unknown. A condition that does not decide the target
(`feature = "x"`, `HAVE_SOUND`, `Platform.Version > 30`) counts as unknown, and the code under it stays in every
target's view. `cg platforms` and `cg coverage` list how many conditions are unknown, with samples.

## In the graph

- `nodes.attrs.platforms`: the project's targets a symbol is built for (absent: every target). An `#else` branch or
  a negated condition lists the project's other targets, not every platform cg knows; `platforms_other` holds the
  known non-target platforms where the condition also holds, for `--platform` on a target the project does not
  declare. `platform_expr` is the source
  condition (`cfg(unix)`, `#if defined(_WIN32)`, `.ios file`, `if (dart.library.io) (lib/sync.dart:1)`),
  `platform_at` its file:line and `platform_unknown` the targets on which it could not be evaluated.
- `edges.attrs.platforms`: the same for a call or reference inside a platform branch (`if (Platform.OS === 'ios')
  { openIosSettings() }`).
- Variant links: a call that resolves to one variant (`storage.ios.ts`, the stub of a conditional import, the
  host's `cfg` in rust-analyzer, a Swift function or method defined once per `#if` branch) is also linked to its sibling variants, with `attrs.platform_variant_of`. `impact`
  on `storage.android.ts#save` finds the callers that import `./storage`. Every reference into a Rust / C / C++ /
  Swift per-platform definition carries `attrs.variant_platforms`, the targets that definition is built for, and the
  divergence check counts any variant of the symbol, so a caller without a condition of its own is not reported as
  missing on the target another variant covers.
- A Swift type defined once per `#if` branch (`#if os(macOS) struct Toolbar { } #else struct Toolbar { } #endif`) is
  one node per branch (`class:Toolbar`, `class:Toolbar@7`); each variant contains its own members
  (`method:Toolbar.show@8`), and a nested type follows its variant. Comments, imports and other declarations between
  the branches do not matter. A member a variant does not define is not missing there when that variant conforms to a
  protocol requiring it, or, for `init`, when its SDK superclass provides it (`attrs.external_supers`). Conditions
  with parentheses (`(os(iOS) && canImport(CoreTelephony)) || os(tvOS)`) are evaluated as written. Kotlin common
  code binds to the `expect` class, members called from an `actual` class bind to that `actual`, and a platform's
  test source set (`iosTest`, `androidUnitTest`, `androidInstrumentedTest`) is built for that platform.
- Rust exact mode: rust-analyzer resolves the host configuration, then runs once per other target the `cfg` conditions
  name (up to 3; `CODEGRAPH_RUST_TARGETS`), so references under another target's `cfg` are exact
  (`attrs.exact_target`). What none resolves is added from the syntax layer (`via: cfg-inactive`), so every target's
  callers are in the graph.
- Re-exports in a variant file count as its definitions: TS `export {a as b} from './m'`, `export {x}`, `export *`,
  `export const X = Y` (module `attrs.reexports`: name -> node id, `reexports_external` for package symbols,
  `reexports_all`), Dart `export 'src/x.dart' show f` and top-level tear-offs (`const f = Impl.f`) in a
  conditional-import library. A call through the variant also reaches the re-exported definition, and API surface /
  missing-callee findings count it.

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
  xcode_membership: true                # tag files only some Xcode targets compile (default true)
```

`cg config show` lists the effective values and where each one comes from.

## Validation

Spot checks on public projects (ripgrep, alacritty, libuv, curl, dart-lang/http, localsend, bluesky social-app) are
in [validation.md](validation.md#platform-specific-code).
