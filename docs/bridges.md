# Web / native bridges

A hybrid or cross-platform app calls into native code by name: a Capacitor plugin method, a React Native / Expo
native module method or a Flutter platform channel method. code-graph links each such call to the Kotlin, Java,
Swift or Objective-C code that receives it on every platform, so `impact`, `downstream`, `tests` and `--platform`
cross the bridge. Desktop apps cross a process boundary the same way: Electron IPC channels and context-bridge
members, and Tauri commands ([below](#desktop-process-boundaries-electron-and-tauri)).

## Model

Every bridge method is an **endpoint** node shared by the sending and the receiving side:

```
JS / Dart caller --SENDS_TO--> endpoint:<protocol>:<module>#<method> --RECEIVED_BY--> native handler (per platform)
```

| protocol | endpoint id | sender | receiver |
|---|---|---|---|
| `capacitor` | `endpoint:capacitor:Echo#echo` | `registerPlugin('Echo')`, `Plugins.Echo`, `Capacitor.Plugins.Echo`, an `@capacitor/*` package export, `Capacitor.nativePromise('Echo', 'echo')` | `@CapacitorPlugin(name = "Echo")` + `@PluginMethod` (Java / Kotlin); a `CAPPlugin` with `jsName` + `CAPPluginMethod` or an Objective-C `CAP_PLUGIN(...)` registration (Swift) |
| `react-native` | `endpoint:react-native:CalendarModule#createEvent` | `NativeModules.X`, destructuring from `NativeModules`, `TurboModuleRegistry.get[Enforcing]('X')`, `require('./NativeX').default`, Expo `requireNativeModule('X')` (`attrs.api = expo-modules`) | `getName()` modules with `@ReactMethod`, `Native*Spec` overrides (Java / Kotlin); `RCT_EXPORT_MODULE` / `RCT_EXPORT_METHOD` / `RCT_REMAP_METHOD` (Objective-C); `RCT_EXTERN_MODULE` / `RCT_EXTERN_METHOD` mapped to the Swift method; Expo `Name("X")` + `Function` / `AsyncFunction` |
| `flutter` | `endpoint:flutter:samples.flutter.dev/battery#getBatteryLevel` | `MethodChannel('name').invokeMethod('m')` (also `invokeMapMethod` / `invokeListMethod`), with the channel resolved through locals, fields, statics, top-level constants, getters and `late` fields | the `MethodChannel` / `FlutterMethodChannel` / `methodChannelWithName:` handler: `call.method == "m"`, `when` / `switch` cases, `isEqualToString:` |
| `flutter-event` | `endpoint:flutter-event:<channel>` | `EventChannel('name').receiveBroadcastStream()` | the channel's `StreamHandler` |

The ids follow the shared protocol endpoint model of the protocol epic
([#29](https://github.com/cyberchronos00/code-graph/issues/29) /
[#31](https://github.com/cyberchronos00/code-graph/issues/31)): `endpoint:<protocol>:<name>` nodes with SENDS_TO
and RECEIVED_BY edges, so other protocols (IPC, message queues) use the same kinds.

- **SENDS_TO** (code → endpoint): `attrs.role = invoke`, `via` (how the module was reached, e.g. `["NativeModules"]`,
  `["TurboModuleRegistry", "require"]`), `module_at` (where the module object was created), `external` (the npm
  package of a plugin implemented outside the repo). A call from a test file is a TEST_CALLS edge with
  `attrs.orig = SENDS_TO`, so `tests` finds it but it never counts as a caller.
- **RECEIVED_BY** (endpoint → handler): `attrs.platform` (android, ios, macos; from `android/`, `ios/`, `macos/`,
  `androidMain/`, `iosMain/` paths, else from the language), `via` (the registration form).
- Both propagate: `impact` on a native method lists the JS / Dart code that sends to it, and `downstream` from a
  screen reaches the native handlers on every platform. `--platform android` keeps only the Android receivers.
- Endpoint attrs: `protocol`, `transport = local`, `namespace` (module / plugin / channel), `method`,
  `platforms_received`, `side` (send, receive, both), `checks`, `missing_on`, `external` + `package`,
  `base_method`, `sender_platforms`.

Receivers in Kotlin and Swift files are the plugin's method nodes. Java and Objective-C files have no language
plugin yet: their receiving methods become small stub nodes (`method:<package>.<Class>.<method>` for Java,
`method:objc:<Class>.<method>` for Objective-C, `attrs.bridge_stub`), and the same stub form is used for a Kotlin
or Swift method the plugin did not index. Receiver files are marked platform-specific, so `cg platforms` and
`--platform` see them.

## Desktop process boundaries: Electron and Tauri

An Electron or Tauri app is one program split over processes. Calls across them use the same endpoint model, with
the process role (`main`, `preload`, `renderer` for Electron; `webview`, `core` for Tauri) in place of a platform:

| protocol | endpoint id | sender | receiver |
|---|---|---|---|
| `electron-ipc` | `endpoint:electron-ipc:settings:read` (the channel) | `ipcRenderer.invoke` / `send` / `sendSync` / `postMessage` (renderer, preload); `webContents.send`, `event.sender.send`, a `WebFrameMain` `send` (main) | `ipcMain.handle` / `handleOnce` / `on` / `once` (main); `ipcRenderer.on` / `once` (renderer) |
| `electron-preload` | `endpoint:electron-preload:api#readSettings` | `window.api.readSettings()` (also `globalThis` / `self`, and `const api = window.api; api.readSettings()`) | the member of `contextBridge.exposeInMainWorld('api', {readSettings: ...})` in the preload script |
| `tauri` | `endpoint:tauri:greet`; plugin commands `endpoint:tauri:plugin:fs\|read_text_file` | `invoke('greet', args)` from `@tauri-apps/api/core` (v2) or `@tauri-apps/api/tauri` (v1), `window.__TAURI__.core.invoke` | the Rust `#[tauri::command] fn greet` (or `#[command]` with `use tauri::command`) |

- Channels are string literals, `const`s or enum members (`ipcMain.handle(IpcEvents.GET_FILES, fn)`). A project
  wrapper named after the Electron object (`ipcMainManager.handle(...)`, `ipcRendererManager.send(...)`) counts at
  `heuristic` confidence. A handler outside the project (`ipcMain.on('quit', app.quit)`) makes the registering
  function the receiver.
- A listener whose channel is a variable typed as a union of literals (`ipcRenderer.on(table[type], fn)` over a
  lookup table, a relay `ipcMain.on(name, ...)` over `IpcEvents[]`) receives each member at `heuristic` confidence,
  but only channels another process sends and that this process does not already receive by name.
- Handlers registered in test files are not receivers. Inline handlers become function nodes named after the
  registration (`ipcMain.handle('settings:read')`), so `impact` on the code they call reaches the renderer.
- Tauri commands are listed in `generate_handler![...]`; a command missing from it gets the check `unregistered`.
  Commands registered by a plugin crate in the repo (`tauri::plugin::Builder::new("x")`) are
  `endpoint:tauri:plugin:x|<command>`; a `plugin:x|cmd` call to a plugin that is not in the repo is `external`
  (package `tauri-plugin-x`). A Tauri app without a root `Cargo.toml` has its Rust core indexed from
  `<app>/src-tauri/Cargo.toml`.
- `no_receiver` here means the other side is in the repo (some channel / command is received) but not this one.
  There is no `missing_on`.
- The module nodes of the files taking part get `attrs.process` (main, preload, renderer, webview, core), and the
  edges carry `attrs.process` of their side. Endpoint `transport` is `ipc` for `electron-ipc` / `tauri` and
  `local` for `electron-preload`.

```
$ cg bridges --protocol tauri --db out/app.db      # tests/bridge_fixtures/tauri_app
tauri: 6 endpoint(s), 4 linked  (external 1, no_receiver 1, unregistered 1)
tauri:greet  received in: core
    sent by greet (main.ts) @ src/main.ts:4 [exact, webview]
    received by tauri_fixture::greet @ src-tauri/src/main.rs:7 [core, #[tauri::command]]
tauri:gret  received in: -  ! NO RECEIVER (this app handles other channels / commands, not this one)
    sent by typo (main.ts) @ src/main.ts:12 [exact, webview]
tauri:increment  received in: core
    sent by counter (main.ts) @ src/main.ts:8 [exact, webview]
    received by tauri_fixture::commands::increment @ src-tauri/src/commands.rs:6 [core, #[tauri::command]]
tauri:plugin:app-menu|popup  received in: core
    sent by showMenu (main.ts) @ src/main.ts:24 [exact, webview]
    received by tauri_fixture::menu::popup @ src-tauri/src/menu.rs:8 [core, #[tauri::command]]
tauri:plugin:fs|read_text_file  received in: -  ! external (tauri-plugin-fs)
    sent by readText (main.ts) @ src/main.ts:16 [exact, webview]
tauri:secret  received in: core  ! NOT REGISTERED (missing from generate_handler!)
    sent by hidden (main.ts) @ src/main.ts:20 [exact, webview]
    received by tauri_fixture::commands::secret @ src-tauri/src/commands.rs:16 [core, #[tauri::command]]
```

## Checks

| check | meaning |
|---|---|
| `missing_on` | the method is received on some platforms but not on all targets that implement the module (for example a Kotlin `@ReactMethod` without the Objective-C export) |
| `no_receiver` | the module is implemented in the repo but this method is not |
| `external` | nothing in the repo implements the module (a published plugin package; `package` names it) |
| `no_sender` / `test_sender_only` | a native method no JS / Dart code calls (library repos: expected) |

Expected platforms are the project's mobile targets (android, ios; macos only when some bridge module is
implemented for it). They are narrowed by:

- `codegenConfig.platforms` in the module's `package.json` (a React Native module declared for android only);
- for Flutter, the platform folders next to the sending app's `pubspec.yaml` (an iOS-only sample app);
- the senders' own platform conditions: when every call is under `Platform.OS === 'android'` (or another
  [platform condition](platforms.md)), only those platforms need a receiver (`sender_platforms`);
- base methods the framework implements on every platform (Capacitor `checkPermissions`, `requestPermissions`,
  `addListener`, `removeListener`, `removeAllListeners`; React Native `addListener`, `removeListeners`,
  `getConstants`): `base_method`, no `missing_on` / `no_receiver` / `no_sender`.

A Capacitor project's targets come from `capacitor.config.{ts,js,json}` with `android/` / `ios/` next to it (plus
web). Members merged onto a module with `Object.assign(NativeModule, { helper })` are JS helpers, not endpoints.
Local packages (`"x": "file:./libraries/x"` / `link:` dependencies and workspace packages) resolve to their source
when `node_modules` is not installed, so an app calling a module wrapped in such a package still links. Calls made
inside a local module directory (an Expo `modules/` wrapper function that calls the native module) are senders only
when that directory is indexed: list it in `.cg.yaml` `include` (`include: [modules]`).

## Query

```
cg bridges [PATTERN] [--protocol P] [--unmatched] [--json] --db out/app.db
```

`PATTERN` is an endpoint name, a substring or a glob (`Echo#echo`, `Echo`, `samples.flutter.dev/*`). Each
endpoint lists its senders (with entry points reaching them), test senders and receivers per platform, and the
checks. The MCP server has the same `bridges(pattern?, protocol?, unmatched?)` tool. `impact`, `downstream`,
`path` and `tests` accept endpoint ids (`endpoint:react-native:CalendarModule#createEvent`) and native methods
(`CalendarModule.createEvent`, `com.rnapp.CalendarModule.createEvent`).

```
$ cg bridges Echo --db out/app.db      # tests/bridge_fixtures/capacitor_app, abridged
capacitor: 4 endpoint(s), 2 linked  (no_receiver 1, test_sender_only 1, missing_on 1)
mobile targets: ios, android
capacitor:Echo#echo  received on: android, ios
    sent by greet (app.ts) @ src/app.ts:6 [exact]
    received by com.example.app.EchoPlugin.echo @ android/app/src/main/java/com/example/app/EchoPlugin.java:13 [android, @PluginMethod]  (stub: no language plugin)
    received by EchoPlugin.echo @ ios/App/App/EchoPlugin.swift:13 [ios, CAPPluginMethod]
capacitor:Echo#nowhere  received on: -  ! NO NATIVE RECEIVER (the module is implemented here, this method is not)
    sent by lost (app.ts) @ src/app.ts:15 [exact]
capacitor:Echo#vibrate  received on: android  ! MISSING ON ios
    sent by buzz (app.ts) @ src/app.ts:11 [exact]
```

## Not covered yet

- Pigeon-generated APIs and `BasicMessageChannel`; calls from native into Dart / JS (a native `invokeMethod`
  received by `setMethodCallHandler`, React Native events); Cordova plugins; native UI components
  (`requireNativeComponent`, view managers, Expo views).
- Java and Objective-C are scanned for bridge registrations only (stub receivers, no call graph inside them).
- Dynamic module or method names (a variable passed to `NativeModules[name]` or `invokeMethod(name)`) are skipped.
- Electron: `MessagePort` / `utilityProcess` / `webContents.ipc` messaging, preload event subscriptions mapped through
  a lookup table on the renderer side (`window.api.addEventListener('run')` to the channel `table['run']`), and
  `ipcRenderer.removeListener` are not modelled. Tauri: events (`emit` / `listen`), channels, commands invoked from
  Svelte / Vue templates outside `<script>` blocks the TypeScript extractor does not read (`.svelte` files), and
  `generate_handler!` built by macros.

See [validation.md](validation.md#web--native-bridges) for results on public repositories.
