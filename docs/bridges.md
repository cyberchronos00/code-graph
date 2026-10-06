# Web / native bridges

A hybrid app calls native code by name. cg links each call to the Kotlin, Java, Swift or
Objective-C handler on every platform, so `impact`, `downstream`, `tests` and `--platform`
cross the bridge. Electron IPC and Tauri commands use the same endpoint model across processes.

## Model

```
JS / Dart caller --SENDS_TO--> endpoint:<protocol>:<module>#<method> --RECEIVED_BY--> native handler
```

| protocol | id | sender | receiver |
|---|---|---|---|
| `capacitor` | `endpoint:capacitor:Echo#echo` | `registerPlugin('Echo')`, `Capacitor.Plugins.Echo`, `nativePromise('Echo', 'echo')` | `@CapacitorPlugin` + `@PluginMethod`; Swift `CAPPlugin` / `CAP_PLUGIN` |
| `react-native` | `endpoint:react-native:CalendarModule#createEvent` | `NativeModules.X`, `TurboModuleRegistry.get`, Expo `requireNativeModule` | `@ReactMethod` / `Native*Spec`; `RCT_EXPORT_METHOD`; Expo `Function` / `AsyncFunction` |
| `flutter` | `endpoint:flutter:<channel>#<method>` | `MethodChannel.invokeMethod` (channel resolved through locals, fields, constants) | `call.method == "m"` / `when` / `switch` on that channel |
| `flutter-event` | `endpoint:flutter-event:<channel>` | `EventChannel.receiveBroadcastStream()` | the channel's `StreamHandler` |
| `cordova` | `endpoint:cordova:Toast#show` | `cordova.exec(..., 'Toast', 'show', ...)` (`www/` next to `plugin.xml` is indexed) | `execute` comparing `action`, or a `CDVPlugin` method. Service name from `<feature name>` |
| `pigeon` | `endpoint:pigeon:NativeSyncApi#hashAssets` | a call on the generated `@HostApi()` class, including Riverpod `ref.read` of a `Provider<Api>` | the class that implements the generated interface. Definition files (`package:pigeon/`) supply the API when `*.g.dart` is git-ignored |

`direction = to_app` (the rest are `to_native`): a native `invokeMethod` on a known channel, a
Pigeon `@FlutterApi()` call, and native events.

| event id | from | to |
|---|---|---|
| `endpoint:react-native-event:<event>` | `emitDeviceEvent`, `sendEventWithName`, Expo `sendEvent`, a helper that forwards its event-name parameter | `addListener` on `NativeEventEmitter` / `DeviceEventEmitter` / an Expo module |
| `endpoint:capacitor-event:<Plugin>#<event>` | `notifyListeners("evt")` | `Plugin.addListener` |

Event names may be constants, string enums (`rawValue`, `val event`) or a computed property
whose every return is a literal. React Native's own events (`keyboardDidShow`) and JS-only
`DeviceEventEmitter.emit` are not endpoints. `platforms_sending` lists who sends; `missing_on`
is not computed. No JS listener is `no_listener` (not a check). A listener of an in-repo module
with no native sender is `no_sender`; a package such as `@capacitor/keyboard` is `external`.

`SENDS_TO` (code → endpoint) carries `role = invoke`, `via` (how the module was reached:
`NativeModules`, `TurboModuleRegistry`, `require`), `module_at` and `external` (the npm
package when the plugin is outside the repo). A test file emits `TEST_CALLS` with
`orig = SENDS_TO`, so `tests` finds it and it never counts as a caller. `RECEIVED_BY` carries
`platform` (`android`, `ios`, `macos`; from `android/`, `ios/`, `macos/`, `androidMain/`, `iosMain/`, else the language) and `via` (the registration form). Both propagate: `impact` on
a native method lists the JS / Dart senders, and `downstream` from a screen reaches every
platform. `--platform android` keeps only the Android receivers.

Endpoint attrs: `protocol`, `transport = local`, `namespace` (module / plugin / channel),
`method`, `platforms_received`, `side`, `checks`, `missing_on`, `external`, `package`,
`base_method`, `sender_platforms`, `direction` (`to_native` or `to_app`),
`platforms_sending`.

Objective-C has no language plugin. Java methods are indexed by the [Java](java.md) plugin when
the file is in the graph. A method that plugin did not index is still a stub
(`method:<package>.<Class>.<method>`, `attrs.bridge_stub`), as are Objective-C methods
(`method:objc:<Class>.<method>`). The same stub is used when a Kotlin or Swift method was not
indexed. Receiver files are platform-specific, so `cg platforms` sees them.

## Electron and Tauri

Process role replaces platform: Electron `main` / `preload` / `renderer`; Tauri `webview` /
`core`.

| protocol | id | sender | receiver |
|---|---|---|---|
| `electron-ipc` | `endpoint:electron-ipc:settings:read` | `ipcRenderer.invoke` / `send`; `webContents.send` | `ipcMain.handle` / `on`; `ipcRenderer.on` |
| `electron-preload` | `endpoint:electron-preload:api#readSettings` | `window.api.readSettings()` | `contextBridge.exposeInMainWorld('api', {readSettings})` |
| `tauri` | `endpoint:tauri:greet`; plugins `endpoint:tauri:plugin:fs\|read_text_file` | `invoke('greet')` (`@tauri-apps/api` v1 or v2) | `#[tauri::command] fn greet` |

Channels are string literals, `const`s or enum members (
`ipcMain.handle(IpcEvents.GET_FILES, fn)`). A project wrapper named after the Electron object (
`ipcMainManager.handle`, `ipcRendererManager.send`) counts at `heuristic` confidence. A
handler outside the project (`ipcMain.on('quit', app.quit)`) makes the registering function
the receiver. A listener whose channel is a variable typed as a union of literals (a lookup
table, a relay over `IpcEvents[]`) receives each member at `heuristic` confidence, but only
channels another process sends and that this process does not already receive by name. Handlers
registered in test files are not receivers. Inline handlers become function nodes named after
the registration (`ipcMain.handle('settings:read')`), so `impact` on the code they call
reaches the renderer.

Tauri commands are listed in `generate_handler![...]`. A command missing from it gets
`unregistered`. Commands registered by a plugin crate in the repo (
`tauri::plugin::Builder::new("x")`) are `endpoint:tauri:plugin:x|<command>`. A name registered
more than once (the app's `get` and a plugin's `get`) takes the `generate_handler!`
registration closest to the command's file (longest common directory). A `plugin:x|cmd` call to
a plugin that is not in the repo is `external` (package `tauri-plugin-x`). A Tauri app without
a root `Cargo.toml` has its Rust core indexed from `<app>/src-tauri/Cargo.toml`. `no_receiver`
here means some other channel or command is received in this repo, but not this one. There is no
`missing_on`. The module nodes of the files taking part get `attrs.process` (main, preload,
renderer, webview, core), and the edges carry `attrs.process` of their side. Endpoint
`transport` is `ipc` for `electron-ipc` / `tauri` and `local` for `electron-preload`.

```text
$ cg bridges --protocol tauri --db out/app.db      # tests/bridge_fixtures/tauri_app
tauri: 6 endpoint(s), 4 linked  (external 1, no_receiver 1, unregistered 1)
tauri:greet  received in: core
    sent by greet (main.ts) @ src/main.ts:4 [exact, webview]
    received by tauri_fixture::greet @ src-tauri/src/main.rs:7 [core, #[tauri::command]]
tauri:gret  ! NO RECEIVER (this app handles other commands, not this one)
tauri:plugin:fs|read_text_file  ! external (tauri-plugin-fs)
tauri:secret  ! NOT REGISTERED (missing from generate_handler!)
    received by tauri_fixture::commands::secret @ src-tauri/src/commands.rs:16
```

## Checks

| check | meaning |
|---|---|
| `missing_on` | received on some targets that implement the module, not all |
| `no_receiver` | the module is in the repo; this method is not |
| `external` | a published package implements it (`package` names it) |
| `no_sender` / `test_sender_only` | nothing in app code calls it (expected in a library repo) |

Expected platforms are android and ios, plus macos when some module is implemented for it.
Narrowed by `codegenConfig.platforms`, Flutter platform folders next to `pubspec.yaml`, and
senders that all sit under `Platform.OS === 'android'` (`sender_platforms`). Framework base
methods (`checkPermissions`, `addListener`, `removeListeners`, Capacitor
`removeAllListeners`, React Native `getConstants`) are `base_method` and are not checked.

Capacitor targets come from `capacitor.config.*` plus `android/` / `ios/` and web.
`Object.assign(NativeModule, { helper })` is JS, not an endpoint. `file:` / `link:` / workspace
packages resolve without `node_modules`. A local Expo `modules/` wrapper is a sender only when
that directory is in `.cg.yaml` `include`.

## Query

```bash
cg bridges [PATTERN] [--protocol P] [--unmatched] [--json] --db out/app.db
```

`PATTERN` is a name, substring or glob (`Echo#echo`, `samples.flutter.dev/*`). Dynamic names
(`NativeModules[name]`, `invokeMethod(method)`, `cordova.exec` with a variable service) are
listed as `unresolved` (`stats.bridges.dynamic`), not dropped. MCP:
`bridges(pattern?, protocol?, unmatched?)`. `impact` and `path` accept
`endpoint:react-native:CalendarModule#createEvent` and `CalendarModule.createEvent`.

```text
$ cg bridges Echo --db out/app.db      # tests/bridge_fixtures/capacitor_app
capacitor: 4 endpoint(s), 2 linked  (no_receiver 1, test_sender_only 1, missing_on 1)
mobile targets: ios, android
capacitor:Echo#echo  received on: android, ios
    sent by greet (app.ts) @ src/app.ts:6 [exact]
    received by EchoPlugin.echo @ ios/.../EchoPlugin.swift:13 [ios, CAPPluginMethod]
    received by com.example.app.EchoPlugin.echo @ android/.../EchoPlugin.java:13 [android, stub]
capacitor:Echo#nowhere  ! NO NATIVE RECEIVER (the module is implemented here, this method is not)
capacitor:Echo#vibrate  received on: android  ! MISSING ON ios
```

Calls whose module, method or event name is not a literal (`NativeModules[name].f()`,
`emitter.addListener(evt)`, `cordova.exec` with a variable service,
`notifyListeners(eventName)`, `channel.invokeMethod(method)`, `MethodChannel(name)`) are
listed at the end as `unresolved` (`stats.bridges.dynamic`: a count and up to 20 samples).
`impact`, `downstream`, `path` and `tests` accept endpoint ids (
`endpoint:react-native:CalendarModule#createEvent`) and native methods (
`CalendarModule.createEvent`, `com.rnapp.CalendarModule.createEvent`).

## Not covered yet

- `BasicMessageChannel`, Pigeon `@EventChannelApi`, native UI components, React Native codegen
  `emitOnX`.
- A Java callback that forwards an event name, and a native `invokeMethod` on a channel created
  in another file (unless this file creates exactly one).
- Java is indexed by the language plugin; bridge registration is still a separate scan. Objective-C is scanned for registrations only.
- Electron `MessagePort`, `utilityProcess`, preload listeners mapped through a renderer lookup
  table. Tauri `emit` / `listen`, and commands invoked from `.svelte` / `.vue` outside
  `<script>`.

Public-app results: [web / native bridges](validation-log.md#web--native-bridges).
