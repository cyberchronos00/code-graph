# Platform-specific code

Per-platform code is tagged with the targets it is built for. `--platform ios` drops what that
target does not build. `cg platforms divergence` lists variants that leave a declared target
uncovered, and calls that are live where the callee is absent.

```text
$ cg impact open_logs --db out/app.db --platform windows
platform: windows (4 nodes and 13 references not built for it left out)
not built for windows: function:dirs_demo::open_logs

$ cg platforms divergence --db out/app.db
== VARIANTS: dirs_demo::paths::config_dir  covered: windows, linux, macos
== REFERENCED WHERE THE CALLEE IS NOT BUILT: 1
  main -CALLS-> open_logs  missing on: windows, macos  (callee: linux)
```

## Targets

Known targets: `windows`, `linux`, `macos`, `ios`, `android`, `web`, `tvos`, `watchos`,
`visionos`. `--platform` also takes aliases (`win32`, `darwin`, `wasm`, `xros`, …). A
project's targets come from the first source that names them:

| source | targets |
|---|---|
| `.cg.yaml` `platforms.targets` | exactly those |
| Flutter folders next to `pubspec.yaml` | one per `android/` `ios/` `web/` `macos/` `windows/` `linux/` |
| Expo `app.json` `expo.platforms` | listed (default ios, android) |
| React Native | ios, android, plus web / windows / macos when `react-native-web` / `-windows` / `-macos` is a dependency |
| Kotlin Multiplatform `kotlin { }` | `androidTarget` android, `ios*` ios, `tvos*` / `watchos*` / `macos*` / `linuxX64` / `mingwX64`, `js` / `wasmJs` web, Compose Desktop windows+linux+macos |
| Xcode `*.xcodeproj` | `SUPPORTED_PLATFORMS`, else `SDKROOT`; `SUPPORTS_MACCATALYST = YES` adds macos |
| SwiftPM `platforms:` | the listed ones (macCatalyst counts as macos) |
| Tauri 2 `src-tauri/gen/android` or `gen/apple` | desktop plus android / ios |
| Electron or Tauri | windows, linux, macos |
| anything else with conditions | desktop default (windows, linux, macos), plus a mobile or web target a condition names (`target_os = "android"`, `TARGET_OS_IPHONE` → ios) |

`#if os(Linux)` and a Vapor / Hummingbird dependency add linux to a SwiftPM package. An Xcode
app with no root `Package.swift` uses only the platforms its project lists. `cg platforms`
prints the targets and where each came from.

### Apple

| case | rule |
|---|---|
| visionOS / tvOS / watchOS | own targets. `os(visionOS)` is never iOS. If visionOS is not a project target, that branch is `no known target` and `!os(visionOS)` is every target |
| Mac Catalyst | `SUPPORTS_MACCATALYST` and no AppKit macOS target: on macos, `os(iOS)` and `targetEnvironment(macCatalyst)` are true, `os(macOS)` is false, `canImport(UIKit)` is true. Both AppKit and Catalyst: those conditions are unknown on macos |
| Xcode membership | a file only some targets compile (Sources build phase, Xcode 16 synchronized folders) is tagged with those platforms. `platforms.xcode_membership: false` turns this off. Local-package files are not target members |
| `extension URL` | the type node is never tagged; its members are |

`canImport(UIKit)` is ios, tvos, visionos (and macos in a Catalyst app). `canImport(AppKit)` is
macos. `targetEnvironment(simulator)` is not a platform. `@available(iOS 17, *)` is a minimum OS
version (`attrs.available`, [swift.md](swift.md)), not a target filter.
`@available(iOS, unavailable)` is a platform condition.

## What is recognised

| language | conditions | variants |
|---|---|---|
| Rust | `#[cfg]`, `if cfg!`, `target_os` / `target_family` / `unix` / `windows` / arch / env / vendor | one function per `cfg` (`config_dir` and `config_dir@9`) |
| C / C++ | `#if` on `_WIN32`, `__APPLE__` + `TARGET_OS_IPHONE`, `__linux__`, `__ANDROID__`, `__EMSCRIPTEN__`; dirs `win/` `unix/` `linux/`; names `*_win.c` | one function per branch or per platform file |
| Dart | `Platform.isIOS`, `kIsWeb`, `TargetPlatform`, `switch` / `?:` | `import 'stub.dart' if (dart.library.io) 'io.dart'` |
| TS / JS | `Platform.OS`, `Platform.select`, `process.platform`, `os.platform()` | `.ios.ts` / `.android.ts` / `.native.ts` / `.web.ts` (`moduleSuffixes`) |
| Swift | `#if os(iOS)`, `canImport`, `targetEnvironment(macCatalyst)`, `@available(*, unavailable)` | one type or method per `#if` branch (`class:Toolbar`, `class:Toolbar@7`) |
| Kotlin | `iosMain`, `androidMain`, … | `expect` links to each `actual` ([kotlin.md](kotlin.md)) |

A guard (`if (Platform.OS !== 'ios') return`) tags the rest of the block with the negated
condition. A JS branch without braces ends at its line. Electron and Tauri process roles are
recorded by [bridges](bridges.md#electron-and-tauri). Bridge receiver files take the platform
of `android/` / `ios/` / `macos/`.

A condition that does not decide the target (`feature = "x"`, `Platform.Version > 30`) is
unknown. The code under it stays in every target's view. `cg platforms` and `cg coverage` count
unknown conditions.

## In the graph

| attr | on |
|---|---|
| `platforms` | targets the symbol or edge is built for (absent = every target). An `#else` lists the project's other targets, not every platform cg knows |
| `platforms_other` | non-target platforms where the condition also holds, for `--platform` on an undeclared target |
| `platform_expr`, `platform_at` | source condition and file:line |
| `platform_unknown` | targets the condition did not decide |
| `platform_variant_of` | sibling variants of one call (`storage.ios.ts`, a conditional-import stub, a per-`#if` Swift function) |
| `variant_platforms` | targets that definition is built for. Divergence counts any variant, so a caller with no condition of its own is not "missing" on a target another variant covers |
| `exact_target` | rust-analyzer ran for that cfg (`CODEGRAPH_RUST_TARGETS`, up to 3 non-host targets). What none resolves is added from syntax (`via: cfg-inactive`) |

A Swift member a variant does not define is not missing when the variant conforms to a protocol
that requires it, or when `init` comes from an SDK superclass (`attrs.external_supers`).
Kotlin common code binds to `expect`; a call from an `actual` binds to that `actual`.
`iosTest` / `androidUnitTest` are built for that platform.

Re-exports in a variant file count as its definitions (TS `export {a as b}`, Dart
`export … show`, a top-level tear-off). API-surface and missing-callee findings count them.

## Queries

`reaches`, `impact`, `downstream`, `path`, `routes` and `search` take `--platform TARGET`
(MCP: `platform`). The first line names the filter and how many conditions could not be
evaluated. A target symbol that is not built for the platform is named as such. Without
`--platform` the answer is the full graph, with labels like `[ios, android]`. An unknown name
exits 2 and lists the known targets.

| finding | meaning |
|---|---|
| VARIANTS | one symbol per platform, and which declared targets no variant covers |
| API SURFACE DIFFERS | a variant lacks a symbol its siblings define and importers use |
| REFERENCED WHERE THE CALLEE IS NOT BUILT | a live call or import on a target where the callee and every variant are absent |

Not findings: an import that spells `./Cam.ios` (that file is used on every target the importer
builds for); a test file named `release.web.test.ts`; a Swift call that uses an SDK initializer
where the project's `extension` is not built; C functions of the same name in separate `main()`
programs; a reference to code built for no declared target (`sunos.c`, counted, not listed).

Test code whose platforms are only the project default (no `#if`, no Xcode membership) is
listed under FROM TEST CODE WHOSE PLATFORMS ARE THE PROJECT DEFAULT. A test narrowed by `#if` or
by its target's `SUPPORTED_PLATFORMS` stays in the main missing-callee list.

`--target ios` keeps one target.
`--kind variants|api_surface|missing_callee|missing_callee_tests` keeps one kind. MCP:
`platforms`, `platform_divergence(target, kind)`.

```yaml
platforms:
  targets: [ios, android, web]
  paths:
    "src/win32/**": [windows]
    "src/posix/**": [unix]     # native = every non-web target
  file_suffixes: true          # .ios.ts / .android.ts
  path_conventions: true       # win/ and *_win.c
  xcode_membership: true
```

`cg config show` lists the effective values and where each one came from. `cg index` records
`platforms` in the stats: targets, conditions, tagged symbols and references, per-target counts,
divergence findings, seconds.

```text
$ cg impact function:lib/storage/storage_web.dart#save --db out/app.db --platform ios
platform: ios (5 nodes and 11 references not built for it left out; 0 conditions could not be evaluated)
not built for ios: function:lib/storage/storage_web.dart#save
```

Rust exact mode resolves the host configuration, then runs once per other target the `cfg`
conditions name (up to 3, `CODEGRAPH_RUST_TARGETS`). References under another target's `cfg`
are exact (`attrs.exact_target`). What none of those runs resolves is added from the syntax
layer (`via: cfg-inactive`), so every target's callers are in the graph.

A call that resolves to one variant (`storage.ios.ts`, the stub of a conditional import, the
host `cfg` in rust-analyzer, a Swift function defined once per `#if`) is also linked to its
siblings, with `attrs.platform_variant_of`. `impact` on `storage.android.ts#save` finds callers
that import `./storage`. Re-exports in a variant file count as its definitions: TS
`export {a as b} from './m'`, `export *`, `export const X = Y` (`attrs.reexports`), Dart
`export 'src/x.dart' show f` and a top-level tear-off in a conditional-import library. A call
through the variant reaches the re-exported definition.

Parentheses in a Swift condition (`(os(iOS) && canImport(CoreTelephony)) || os(tvOS)`) are
evaluated as written. Comments and other declarations between `#if` branches do not merge the
types. A nested type follows its variant.

Kingfisher's tests are built for watchOS (`build` only, never `test`), which gives 35
project-default test findings. Spot checks on ripgrep, alacritty, libuv, curl, dart-lang/http,
localsend and bluesky social-app: [validation-log.md](validation-log.md#platform-specific-code)
.
