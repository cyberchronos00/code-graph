# Web / native bridges

A hybrid or cross-platform app calls into native code by name: a Capacitor plugin method, a React Native / Expo
native module method or a Flutter platform channel method. code-graph links each such call to the Kotlin, Java,
Swift or Objective-C code that receives it on every platform, so `impact`, `downstream`, `tests` and `--platform`
cross the bridge.

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

See [validation.md](validation.md#web--native-bridges) for results on public repositories.
