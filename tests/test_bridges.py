"""Web / native bridges (cg_code_graph/bridges.py): Capacitor plugins, React Native / Expo native modules and Flutter
platform channels on both platforms (tests/bridge_fixtures), as SENDS_TO -> endpoint:<protocol>:<module>#<method> ->
RECEIVED_BY edges with per-platform receivers, the missing-platform / no-receiver / external checks, `impact` /
`downstream` / `tests` across the bridge, the --platform filter, `cg bridges` and the native source scanner."""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from cg_code_graph import bridges as B  # noqa: E402
from cg_code_graph import query as Q  # noqa: E402
from cg_code_graph.core.extractors import js_runtime  # noqa: E402
from cg_code_graph.core.model import EDGE_KINDS, PROPAGATING  # noqa: E402
from cg_code_graph.core.store import GraphStore  # noqa: E402
from cg_code_graph.indexer import index_project  # noqa: E402
from cg_code_graph.plugins.dart.plugin import find_dart  # noqa: E402

FX = ROOT / "tests" / "bridge_fixtures"
TS_DEPS = ROOT / "cg_code_graph" / "plugins" / "ts" / "extractor" / "node_modules"
needs_ts = pytest.mark.skipif(not TS_DEPS.exists() or js_runtime() is None,
                              reason="run `npm ci` in cg_code_graph/plugins/ts/extractor and put node (20+) or bun on PATH")
needs_dart = pytest.mark.skipif(find_dart() is None, reason="Dart SDK not found (set $DART or put dart on PATH)")
pytest.importorskip("tree_sitter_kotlin")
pytest.importorskip("tree_sitter_swift")
_G: dict = {}


def graph(name: str) -> tuple[GraphStore, dict, Path]:
    if name not in _G:
        db = Path(tempfile.mkdtemp(prefix="codegraph-bridges-")) / "g.db"
        res = index_project(FX / name, db, name)
        _G[name] = (GraphStore(str(db)), res, db)
    return _G[name]


def endpoints(st: GraphStore) -> dict:
    return {e["id"].split(":", 1)[1]: e for e in B.bridges(st)["endpoints"]}


def recv(e: dict) -> dict:
    return {r["platform"]: r["handler"] for r in e["receivers"]}


def senders(e: dict) -> set:
    return {s["fn"] for s in e["senders"]}


def cli(*args) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-m", "cg_code_graph.cli", *args], cwd=ROOT, capture_output=True, text=True)


def test_edge_kinds_propagate():
    assert "SENDS_TO" in PROPAGATING and "RECEIVED_BY" in PROPAGATING
    assert {"SENDS_TO", "RECEIVED_BY"} <= set(Q.CALL_LIKE)
    assert B.endpoint_key("flutter", "a/b", "m") == "flutter:a/b#m" and B.endpoint_key("flutter-event", "a/b") == "flutter-event:a/b"
    assert "RECEIVED_BY" in EDGE_KINDS


# ------------------------------------------------------------------ Capacitor
@needs_ts
def test_capacitor_endpoints_and_receivers():
    st, res, _ = graph("capacitor_app")
    ep = endpoints(st)
    assert set(ep) == {"capacitor:Echo#echo", "capacitor:Echo#ping", "capacitor:Echo#vibrate", "capacitor:Echo#nowhere",
                       "capacitor:DeviceInfo#getInfo", "capacitor:Camera#getPhoto"}
    echo = ep["capacitor:Echo#echo"]
    # registerPlugin<EchoPlugin>('Echo') exported as default and imported: Java @PluginMethod + Swift CAPPluginMethod
    assert recv(echo) == {"android": "method:com.example.app.EchoPlugin.echo", "ios": "method:EchoPlugin.echo"}
    assert senders(echo) == {"function:src/app.ts#greet"} and echo["senders"][0]["confidence"] == "exact"
    assert echo["receivers"][0]["stub"] is False and st.node("method:com.example.app.EchoPlugin.echo")["lang"] == "java"
    # `const { DeviceInfo } = Plugins`: Kotlin @CapacitorPlugin without a name (class name) + ObjC CAP_PLUGIN -> Swift func
    dev = ep["capacitor:DeviceInfo#getInfo"]
    assert recv(dev) == {"android": "method:com.example.app.DeviceInfo.getInfo", "ios": "method:DeviceInfoPlugin.getInfo"}
    assert senders(dev) == {"function:src/app.ts#deviceInfo"}
    # checks
    assert ep["capacitor:Echo#vibrate"]["missing_on"] == ["ios"] and ep["capacitor:Echo#vibrate"]["checks"] == ["missing_on"]
    assert ep["capacitor:Echo#nowhere"]["checks"] == ["no_receiver"] and not ep["capacitor:Echo#nowhere"]["missing_on"]
    cam = ep["capacitor:Camera#getPhoto"]
    assert cam["external"] and cam["package"] == "@capacitor/camera" and not cam["checks"]
    assert ep["capacitor:Echo#ping"]["checks"] == ["test_sender_only"] and not ep["capacitor:Echo#ping"]["senders"]
    assert "helper" not in {r["handler"].rsplit(".", 1)[-1] for e in ep.values() for r in e["receivers"]}
    br = res["bridges"]
    assert br["endpoints"] == 6 and br["linked"] == 3 and br["targets"] == ["ios", "android"]
    assert br["checks"] == {"external": 1, "missing_on": 1, "no_receiver": 1, "test_sender_only": 1}
    assert res["platforms"]["targets"] == ["ios", "android", "web"]


@needs_ts
def test_capacitor_impact_platform_and_tests():
    st, _, _ = graph("capacitor_app")
    # native -> JS: impact on either receiver lists the JS caller, through the endpoint
    for spec in ("EchoPlugin.echo", "com.example.app.EchoPlugin.echo"):
        imp = Q.impact(st, spec)
        assert "function:src/app.ts#greet" in {c["id"] for c in imp["callers"]}, spec
    # Class.method resolves Kotlin / Swift / Java-stub methods: both platforms' EchoPlugin.echo
    assert set(Q.resolve_targets(st, "EchoPlugin.echo")) == {"method:EchoPlugin.echo", "method:com.example.app.EchoPlugin.echo"}
    # JS -> native: downstream lists the endpoint; per platform, each receiver is reached only on its own target
    down = Q.downstream(st, "function:src/app.ts#greet")
    assert [s["id"] for s in down["sinks"]["endpoint"]] == ["endpoint:capacitor:Echo#echo"] and down["reached"] == 4
    assert Q.downstream(st, "function:src/app.ts#greet", platform="ios")["reached"] == 3
    for plat, other in (("ios", "method:com.example.app.EchoPlugin.echo"), ("android", "method:EchoPlugin.echo")):
        assert not Q.impact(st, other, platform=plat)["callers"]
    ios = Q.impact(st, "method:com.example.app.EchoPlugin.vibrate", platform="ios")
    assert not ios["callers"]
    assert {c["id"] for c in Q.impact(st, "method:com.example.app.EchoPlugin.vibrate", platform="android")["callers"]} == {"function:src/app.ts#buzz"}
    # tests reach the native plugin through TEST_CALLS -> endpoint -> RECEIVED_BY
    t = Q.tests_covering(st, "EchoPlugin.ping")
    assert t["transitive"] and not t["direct"]


@needs_ts
def test_capacitor_cli():
    _, _, db = graph("capacitor_app")
    r = cli("bridges", "--db", str(db), "--unmatched")
    assert r.returncode == 0, r.stderr
    assert "capacitor:Echo#vibrate  received on: android  ! MISSING ON ios" in r.stdout
    assert "NO NATIVE RECEIVER" in r.stdout and "external (@capacitor/camera)" in r.stdout
    assert "capacitor:Echo#echo" not in r.stdout
    j = json.loads(cli("bridges", "Echo#echo", "--db", str(db), "--json").stdout)
    assert [e["id"] for e in j["endpoints"]] == ["endpoint:capacitor:Echo#echo"]
    assert "no bridge endpoint matches" in cli("bridges", "nothing-here", "--db", str(db)).stdout


# ------------------------------------------------------------------ React Native / Expo
@needs_ts
def test_react_native_modules():
    st, res, _ = graph("rn_app")
    ep = endpoints(st)
    assert set(ep) == {"react-native:CalendarModule#createEvent", "react-native:CalendarModule#deleteEvent",
                       "react-native:DeviceStore#getItem", "react-native:DeviceStore#setItem",
                       "react-native:Haptics#impact", "react-native:Haptics#selection",
                       "react-native:CalendarModule#setBarColor", "react-native:Keyboard#dismiss",
                       "react-native:Keyboard#addListener", "react-native:Keyboard#removeListeners"}
    # `const { CalendarModule } = NativeModules` / NativeModules.CalendarModule: Kotlin getName() = NAME, ObjC RCT_EXPORT_MODULE
    cal = ep["react-native:CalendarModule#createEvent"]
    assert recv(cal) == {"android": "method:com.rnapp.CalendarModule.createEvent", "ios": "method:objc:RCTCalendarModule.createEvent"}
    assert ep["react-native:CalendarModule#deleteEvent"]["missing_on"] == ["ios"]
    assert senders(ep["react-native:CalendarModule#deleteEvent"]) == {"function:src/native/Calendar.ts#removeEvent"}
    # turbo ? require('./NativeDeviceStore').default : NativeModules.DeviceStore, re-exported through `X || null`:
    # Java NativeDeviceStoreSpec overrides (stubs) + RCT_EXTERN_METHOD mapped to the Swift @objc func
    ds = ep["react-native:DeviceStore#getItem"]
    assert recv(ds) == {"android": "method:com.rnapp.DeviceStoreModule.getItem", "ios": "method:DeviceStore.getItem"}
    assert senders(ds) == {"function:src/settings.ts#readSetting"}
    assert ep["react-native:DeviceStore#setItem"]["missing_on"] == ["ios"]
    # Expo Modules: requireNativeModule('Haptics') -> Function / AsyncFunction in definition()
    hp = ep["react-native:Haptics#impact"]
    assert hp["api"] == "expo-modules" and set(recv(hp)) == {"android", "ios"}
    assert recv(hp)["ios"] == "method:HapticsModule.definition"
    sel = ep["react-native:Haptics#selection"]
    assert sel["checks"] == ["no_sender", "missing_on"] and sel["missing_on"] == ["ios"]
    assert res["platforms"]["targets"] == ["ios", "android"]
    # a local `file:` package (no node_modules) outside the tsconfig include: imports resolve to its source;
    # Object.assign(module, { onShow }) helpers are not native methods; codegenConfig.platforms ["android"]
    kb = ep["react-native:Keyboard#dismiss"]
    assert senders(kb) == {"function:src/keyboard.ts#hideKeyboard"} and set(recv(kb)) == {"android"}
    assert not kb.get("checks") and not kb.get("missing_on")
    # NativeEventEmitter plumbing is a base method: no missing-platform / no-sender noise
    lst = ep["react-native:Keyboard#addListener"]
    assert lst["base_method"] and not lst.get("checks")
    # every sender gated to `Platform.OS === 'android'`: the android-only receiver is enough
    bar = ep["react-native:CalendarModule#setBarColor"]
    assert bar["sender_platforms"] == ["android"] and not bar.get("missing_on") and not bar.get("checks")


# ------------------------------------------------------------------ Flutter
@needs_dart
def test_flutter_channels():
    st, res, _ = graph("flutter_app")
    ep = endpoints(st)
    assert set(ep) == {"flutter:samples.flutter.dev/battery#getBatteryLevel", "flutter:samples.flutter.dev/battery#startCharging",
                       "flutter:samples.flutter.dev/battery#getDetails", "flutter:samples.flutter.dev/device#getName",
                       "flutter-event:samples.flutter.dev/charging"}
    lvl = ep["flutter:samples.flutter.dev/battery#getBatteryLevel"]
    # static const field channel; Kotlin `if (call.method == ...)` in the setMethodCallHandler lambda, Swift `case`
    assert recv(lvl) == {"android": "method:com.example.app.MainActivity.configureFlutterEngine", "ios": "method:AppDelegate.application"}
    assert senders(lvl) == {"method:lib/battery.dart#Battery.level"}
    assert ep["flutter:samples.flutter.dev/battery#startCharging"]["missing_on"] == ["ios"]
    assert ep["flutter:samples.flutter.dev/battery#getDetails"]["missing_on"] == ["android"]
    # a local channel; the handler is a class (setMethodCallHandler(DeviceHandler())) with `when (call.method)`
    dev = ep["flutter:samples.flutter.dev/device#getName"]
    assert recv(dev) == {"android": "method:com.example.app.DeviceHandler.onMethodCall"}
    assert senders(dev) == {"function:lib/battery.dart#deviceName"}
    # EventChannel(constant) field -> setStreamHandler(ChargingHandler()) -> onListen
    ev = ep["flutter-event:samples.flutter.dev/charging"]
    assert recv(ev) == {"android": "method:com.example.app.ChargingHandler.onListen"} and ev["missing_on"] == ["ios"]
    # the Dart entry point reaches the native handlers (entry tagging crosses the bridge)
    n = {r["entry_kind"] for r in st.q("SELECT entry_kind FROM node_entry WHERE node_id=?", ("method:AppDelegate.application",))}
    assert n
    assert res["plugins"]["dart"]["platform_channel_sends"] == {"flutter": 4, "flutter-event": 1, "unresolved_channel": 0}


# ------------------------------------------------------------------ native scanner units
def _scan(rel: str, src: str):
    from types import SimpleNamespace
    root = Path(tempfile.mkdtemp(prefix="codegraph-bridge-src-"))
    (root / rel).parent.mkdir(parents=True, exist_ok=True)
    (root / rel).write_text(src)
    from cg_code_graph.core.plugin import GraphBuilder
    b = GraphBuilder()
    proj = SimpleNamespace(root=root, options={})
    sc = SimpleNamespace(paths={}, bridge_paths=[rel])
    B.apply(proj, b, sc)
    return {(e.src.split(":", 1)[1], e.dst.split(":", 1)[1], e.attrs.get("platform")) for e in b.edges.values() if e.kind == "RECEIVED_BY"}


def test_scanner_java_flutter_switch_and_comments():
    got = _scan("android/src/main/java/a/b/Plugin.java", '''package a.b;
public class Plugin implements MethodCallHandler {
  public void onAttachedToEngine(FlutterPluginBinding b) {
    channel = new MethodChannel(b.getBinaryMessenger(), "x/plugin");
    channel.setMethodCallHandler(this);
  }
  @Override
  public void onMethodCall(MethodCall call, Result result) {
    switch (call.method) {
      case "one": result.success(1); break;
      case "two": result.success(2); break;
    }
    // if (call.method.equals("commented")) {}
  }
}
''')
    assert got == {("flutter:x/plugin#one", "a.b.Plugin.onMethodCall", "android"), ("flutter:x/plugin#two", "a.b.Plugin.onMethodCall", "android")}


def test_scanner_objc_flutter_and_react_native():
    got = _scan("ios/Classes/P.m", '''@implementation P
+ (void)registerWithRegistrar:(NSObject<FlutterPluginRegistrar>*)registrar {
  FlutterMethodChannel* channel = [FlutterMethodChannel methodChannelWithName:@"x/p" binaryMessenger:[registrar messenger]];
}
- (void)handleMethodCall:(FlutterMethodCall*)call result:(FlutterResult)result {
  if ([@"getVersion" isEqualToString:call.method]) {
    result(@"1");
  }
}
@end
@implementation RCTThing
RCT_EXPORT_MODULE()
RCT_REMAP_METHOD(doIt, doItWith:(NSString *)s) {}
/* RCT_EXPORT_METHOD(commented:(NSString *)s) {} */
@end
''')
    assert ("flutter:x/p#getVersion", "objc:P.handleMethodCall", "ios") in got
    assert ("react-native:Thing#doIt", "objc:RCTThing.doIt", "ios") in got
    assert not any("commented" in g[0] for g in got)


def test_flutter_package_platform_folders(tmp_path):
    # an iOS-only Flutter app in a samples repo: its senders need no android receiver
    (tmp_path / "ios_only" / "lib" / "src").mkdir(parents=True)
    (tmp_path / "ios_only" / "ios").mkdir()
    (tmp_path / "ios_only" / "pubspec.yaml").write_text("name: ios_only\n")
    (tmp_path / "both" / "lib").mkdir(parents=True)
    for p in ("android", "ios"):
        (tmp_path / "both" / p).mkdir()
    (tmp_path / "both" / "pubspec.yaml").write_text("name: both\n")
    cache: dict = {}
    assert B._flutter_folders(tmp_path, "ios_only/lib/src/main.dart", cache) == {"ios"}
    assert B._flutter_folders(tmp_path, "both/lib/main.dart", cache) == {"android", "ios"}
    assert B._flutter_folders(tmp_path, "tool/x.dart", cache) is None


# ------------------------------------------------------------------ Electron / Tauri (process boundaries)
def recv_all(e: dict) -> list:
    return [r["handler"] for r in e["receivers"]]


def nattrs(st, nid) -> dict:
    a = st.node(nid)["attrs"]
    return json.loads(a) if isinstance(a, str) else (a or {})


@needs_ts
def test_electron_ipc_and_context_bridge():
    st, res, db = graph("electron_app")
    ep = endpoints(st)
    assert set(ep) == {"electron-ipc:settings:read", "electron-ipc:settings:save", "electron-ipc:settings:saved",
                       "electron-ipc:settings:missing", "electron-ipc:app:unused", "electron-preload:api#readSettings",
                       "electron-preload:api#saveSettings", "electron-preload:api#missing", "electron-ipc:app:quit",
                       "electron-ipc:fiddle:run", "electron-ipc:theme:changed", "electron-preload:fiddle#quit",
                       "electron-preload:fiddle#addEventListener"}
    rd = ep["electron-ipc:settings:read"]
    assert senders(rd) == {"function:src/preload/preload.ts#readSettings"}
    assert recv(rd) == {None: "function:src/main/main.ts#ipcMain.handle('settings:read')"} and not rd["checks"]
    # main -> renderer: webContents.send -> ipcRenderer.on
    assert recv(ep["electron-ipc:settings:saved"]) == {None: "function:src/preload/preload.ts#ipcRenderer.on('settings:saved')"}
    assert senders(ep["electron-ipc:settings:saved"]) == {"function:src/main/main.ts#notifySaved"}
    # renderer window.api.* -> contextBridge.exposeInMainWorld('api', {...}) members
    assert senders(ep["electron-preload:api#readSettings"]) == {"function:src/renderer/app.ts#loadSettings"}
    assert ep["electron-ipc:settings:missing"]["checks"] == ["no_receiver"]
    assert ep["electron-ipc:app:unused"]["checks"] == ["no_sender"]
    assert nattrs(st, "endpoint:electron-ipc:settings:read")["transport"] == "ipc"
    assert nattrs(st, "endpoint:electron-preload:api#readSettings")["transport"] == "local"
    # enum channels, project wrappers (ipcMainManager.on / send), a handler outside the project (app.quit: the
    # registering function receives), event.sender.send replies, `const api = window.fiddle` aliases
    assert recv(ep["electron-ipc:app:quit"]) == {None: "function:src/main/lifecycle.ts#setupLifecycle"}
    assert senders(ep["electron-ipc:app:quit"]) == {"function:src/preload/events.ts#quit"}
    assert senders(ep["electron-preload:fiddle#quit"]) == {"function:src/renderer/menu.ts#quitApp"}
    # a union-typed channel (`ipcRenderer.on(table[type])`) receives every member another process sends; the main
    # relay `ipcMain.on(name)` over IpcEvents[] is dropped (main receives these by name / only main sends them)
    run = ep["electron-ipc:fiddle:run"]
    assert senders(run) == {"function:src/main/lifecycle.ts#runFiddle"}
    assert set(recv_all(run)) == {"function:src/preload/events.ts#addEventListener.ipcRenderer.on(channel)"}
    assert run["receivers"][0]["confidence"] == "heuristic"
    assert "function:src/preload/events.ts#addEventListener.ipcRenderer.on(channel)" in recv_all(ep["electron-ipc:theme:changed"])
    assert not any("ipc-manager.ts" in h for e in ep.values() for h in recv_all(e))
    # process roles on the module nodes
    roles = {f: nattrs(st, f"module:{f}").get("process") for f in
             ("src/main/main.ts", "src/preload/preload.ts", "src/renderer/app.ts", "src/main/settings.ts")}
    assert roles == {"src/main/main.ts": "main", "src/preload/preload.ts": "preload", "src/renderer/app.ts": "renderer",
                     "src/main/settings.ts": None}
    # impact crosses renderer -> preload -> main
    callers = {c["id"] for c in Q.impact(st, "function:src/main/settings.ts#readSettings")["callers"]}
    assert {"function:src/preload/preload.ts#readSettings", "function:src/renderer/app.ts#loadSettings"} <= callers
    r = cli("bridges", "--db", str(db), "--protocol", "electron-ipc", "--unmatched")
    assert r.returncode == 0, r.stderr
    assert "electron-ipc:settings:missing" in r.stdout and "electron-ipc:settings:read" not in r.stdout


@needs_ts
def test_tauri_commands(monkeypatch):
    monkeypatch.setenv("CG_RUST_SCIP", "0")
    st, res, _ = graph("tauri_app")
    ep = endpoints(st)
    assert set(ep) == {"tauri:greet", "tauri:increment", "tauri:gret", "tauri:secret", "tauri:plugin:fs|read_text_file",
                       "tauri:plugin:app-menu|popup"}
    # a plugin crate's command (Builder::new("app-menu") ... generate_handler![#![plugin(app_menu)] popup])
    assert recv(ep["tauri:plugin:app-menu|popup"]) == {None: "function:tauri_fixture::menu::popup"}
    assert senders(ep["tauri:plugin:app-menu|popup"]) == {"function:src/main.ts#showMenu"}
    assert recv(ep["tauri:greet"]) == {None: "function:tauri_fixture::greet"}
    assert senders(ep["tauri:greet"]) == {"function:src/main.ts#greet"} and not ep["tauri:greet"]["checks"]
    # bare #[command] with `use tauri::command`, registered as commands::increment
    assert recv(ep["tauri:increment"]) == {None: "function:tauri_fixture::commands::increment"}
    assert ep["tauri:gret"]["checks"] == ["no_receiver"]
    assert ep["tauri:secret"]["checks"] == ["unregistered"]
    fs = ep["tauri:plugin:fs|read_text_file"]
    assert fs["external"] and fs["package"] == "tauri-plugin-fs" and not fs["checks"]
    assert res["bridges"]["tauri"] == {"commands": 4, "registered": 3}
    assert nattrs(st, "crate:tauri_fixture")["process"] == "core"
    assert nattrs(st, "module:src/main.ts")["process"] == "webview"
    # impact of the Rust command lists the webview caller
    assert "function:src/main.ts#counter" in {c["id"] for c in Q.impact(st, "tauri_fixture::commands::increment")["callers"]}


@needs_ts
def test_native_events_cordova_and_dynamic_names():
    """#61: native -> JS events (React Native sendEvent / RCTDeviceEventEmitter.emit / sendEventWithName, Capacitor
    notifyListeners), Cordova cordova.exec -> CordovaPlugin.execute / CDVPlugin methods, and dynamic bridge names."""
    st, res, db = graph("events_app")
    eps = endpoints(st)
    dp = eps["react-native-event:downloadProgress"]
    assert dp["direction"] == "to_app" and not dp["checks"]
    assert {s["fn"] for s in dp["senders"]} == {"method:com.evapp.DownloaderModule.start", "method:objc:Downloader.start"}
    assert sorted(dp["platforms_sending"]) == ["android", "ios"]
    assert any("watchDownloads" in r["handler"] for r in dp["receivers"])
    # the constant EVT_DONE, emitted from a private helper of the module
    assert senders(eps["react-native-event:downloadDone"]) == {"method:com.evapp.DownloaderModule.finish"}
    assert eps["react-native-event:downloadCancelled"]["checks"] == ["no_sender"]     # the module is implemented here
    assert not eps["react-native-event:neverEmitted"]["checks"]     # DeviceEventEmitter: maybe an in-app JS event bus
    assert "react-native-event:keyboardDidShow" not in eps          # React Native's own event
    assert not any(k.startswith("react-native-event:eventName") for k in eps)   # the helper's parameter
    loc = eps["capacitor-event:Geo#locationChanged"]
    assert senders(loc) == {"method:com.evapp.GeoPlugin.onLocation"} and loc["receivers"] and not loc["checks"]
    assert eps["capacitor-event:Geo#geoMissing"]["checks"] == ["no_sender"]
    kb = eps["capacitor-event:Keyboard#keyboardWillShow"]
    assert kb["external"] and kb["package"] == "@capacitor/keyboard" and not kb["checks"]
    show = eps["cordova:Toast#show"]
    assert sorted(recv(show)) == ["android", "ios"] and show["senders"] and not show["checks"]
    assert recv(show)["ios"] == "method:objc:CDVToast.show" and recv(show)["android"] == "method:com.acme.toast.ToastPlugin.execute"
    assert eps["cordova:Toast#vibrate"]["checks"] == ["no_receiver"]
    out = B.bridges(st)
    whats = {d["what"] for d in out["unresolved"]}
    assert "NativeModules[name].start" in whats and any("addListener(<dynamic>)" in w for w in whats)
    assert out["stats"]["dynamic"]["count"] == 2
    txt = B.render_bridges(out)
    assert "unresolved (name not a literal cg can evaluate): 2" in txt and "no native sender" in txt
    assert "sent from: android, ios" in txt


def test_scanner_cordova_and_event_helpers():
    from cg_code_graph.bridges import NativeFile, scan_cordova
    nf = NativeFile("src/android/X.java", 'package p;\nclass X extends CordovaPlugin {\n static final String A = "go";\n'
                    ' public boolean execute(String action, JSONArray a, CallbackContext c) {\n'
                    '  switch (action) {\n   case "stop": return true;\n  }\n  if (A.equals(action)) return true;\n'
                    '  return false; }\n}\n')
    out = []
    scan_cordova(nf, out, {})
    assert sorted(r.method for r in out) == ["go", "stop"] and {r.namespace for r in out} == {"X"}
    nf = NativeFile("ios/P.swift", "@objc(CDVPay) class Pay: CDVPlugin {\n  @objc func pay(_ command: CDVInvokedUrlCommand) {}\n"
                    "  func helper() {}\n}\n")
    out = []
    scan_cordova(nf, out, {"CDVPay": "Pay"})
    assert [(r.namespace, r.method, r.conf) for r in out] == [("Pay", "pay", "resolved")]


def test_event_names_from_string_enums():
    # #95: `sendEvent(name: Event.keyPressed.rawValue)` (Swift String enum) and `Events.SAVED.event` (Kotlin enum class
    # with a String property) evaluate to the case's value
    from cg_code_graph.bridges import NativeFile, _enum_consts, _str_value
    sw = ('enum Event: String, CaseIterable {\n    case keyPressed, keyReleased = "released"\n'
          '    case other // note\n    var x: Int { 1 }\n}\n')
    kt = 'enum class Events(val event: String) {\n    SAVE_ERROR("SaveError"),\n    SPLIT("SplitViewChanged")\n}\n'
    c = {**_enum_consts("ios/Events.swift", sw), **_enum_consts("android/Events.kt", kt)}
    assert c == {"Event.keyPressed.rawValue": "keyPressed", "Event.keyReleased.rawValue": "released",
                 "Event.other.rawValue": "other", "Events.SAVE_ERROR.event": "SaveError",
                 "Events.SPLIT.event": "SplitViewChanged"}
    nf = NativeFile("ios/W.swift", "class W {}\n")
    assert _str_value(nf, "name: Event.keyReleased.rawValue", c) == "released"
    assert _str_value(nf, "Events.SPLIT.event", c) == "SplitViewChanged"
    assert _enum_consts("ios/E.swift", "enum Mode: Int { case a, b }\n") == {}


@needs_dart
def test_flutter_dynamic_method_names_are_listed(tmp_path):
    # #95: `invokeMethod(name)` with a name cg cannot evaluate, or a channel built from a parameter -> `unresolved`
    (tmp_path / "lib").mkdir()
    (tmp_path / "pubspec.yaml").write_text("name: dyn_app\nenvironment:\n  sdk: '>=3.0.0 <4.0.0'\n")
    (tmp_path / "lib" / "dyn.dart").write_text(
        "import 'package:flutter/services.dart';\n\n"
        "class Native {\n"
        "  static const _ch = MethodChannel('acme/native');\n"
        "  Future<void> ping() => _ch.invokeMethod('ping');\n"
        "  Future<T?> call<T>(String method) => _ch.invokeMethod<T>(method);\n"
        "  Future<void> other(String name) => MethodChannel(name).invokeMethod('x');\n"
        "}\n")
    db = tmp_path / "g.db"
    index_project(tmp_path, db, "dyn_app")
    out = B.bridges(GraphStore(str(db)))
    assert "flutter:acme/native#ping" in {e["id"].split(":", 1)[1] for e in out["endpoints"]}
    got = sorted((d["protocol"], d["what"], d["line"]) for d in out["unresolved"])
    assert got == [("flutter", "MethodChannel(<dynamic>).invokeMethod", 7),
                   ("flutter", "acme/native.invokeMethod(<dynamic>)", 6)]


@needs_ts
def test_kotlin_event_helper_forwarding_its_parameter(tmp_path):
    # #95: `fun sendJSEvent(eventName: String, ..) { context?.emitDeviceEvent(eventName, data) }` called as
    # `RNUtilsModuleImpl.sendJSEvent(Events.SPLIT.event, map)` -> a send of SplitViewChanged from the caller
    and_ = tmp_path / "android" / "src" / "main" / "java" / "com" / "x"
    and_.mkdir(parents=True)
    (tmp_path / "package.json").write_text('{"name": "helper-app", "dependencies": {"react-native": "0.76.0"}}\n')
    (tmp_path / "tsconfig.json").write_text('{"compilerOptions": {"strict": true}, "include": ["src"]}\n')
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "split.ts").write_text(
        "import {DeviceEventEmitter} from 'react-native';\n"
        "export function watchSplit(cb: (v: unknown) => void) {\n"
        "  return DeviceEventEmitter.addListener('SplitViewChanged', cb);\n}\n")
    (and_ / "Events.kt").write_text('package com.x\n\nenum class Events(val event: String) {\n    SPLIT("SplitViewChanged")\n}\n')
    (and_ / "RNUtilsModuleImpl.kt").write_text(
        "package com.x\n\nimport com.facebook.react.bridge.ReactApplicationContext\n\n"
        "class RNUtilsModuleImpl(reactContext: ReactApplicationContext) {\n"
        "    companion object {\n"
        "        private var context: ReactApplicationContext? = null\n"
        "        fun sendJSEvent(eventName: String, data: Any?) {\n"
        "            context?.emitDeviceEvent(eventName, data)\n"
        "        }\n    }\n}\n")
    (and_ / "SplitView.kt").write_text(
        "package com.x\n\nobject SplitView {\n"
        "    fun changed(map: Any?) {\n"
        "        RNUtilsModuleImpl.sendJSEvent(Events.SPLIT.event, map)\n"
        "    }\n}\n")
    db = tmp_path / "g.db"
    index_project(tmp_path, db, "helper-app")
    st = GraphStore(str(db))
    ep = endpoints(st)["react-native-event:SplitViewChanged"]
    assert senders(ep) == {"method:com.x.SplitView.changed"} and ep["receivers"] and not ep["checks"]
    assert not B.bridges(st)["unresolved"]


def test_event_names_from_locals_and_computed_properties():
    # #95: `notifyListeners(event.listenerEvent, ..)` with a Swift computed `var listenerEvent: String { switch ... }`
    # (each returned literal), and `let event = self?.visibilityChanged` before `notifyListeners(event, ..)`
    from cg_code_graph.bridges import NativeFile, _event_values, _str_props
    sw = ('public class P: CAPPlugin {\n    private let visibilityChanged = "statusBarVisibilityChanged"\n'
          '    @objc func hide(_ call: CAPPluginCall) {\n        guard let event = self?.visibilityChanged else { return }\n'
          '        self?.notifyListeners(event, data: [:])\n    }\n'
          '    func other() { self.notifyListeners(e.listenerEvent, data: nil) }\n}\n'
          'enum BrowserEvent {\n    case loaded, finished\n    var listenerEvent: String {\n        switch self {\n'
          '        case .loaded:\n            return "browserPageLoaded"\n        case .finished:\n'
          '            return "browserFinished"\n        }\n    }\n'
          '    var label: String { if x { return "a" }; return name }\n}\n')
    nf = NativeFile("ios/P.swift", sw)
    props = _str_props(nf)
    assert props == {"listenerEvent": ["browserFinished", "browserPageLoaded"]}      # `label` returns a non-literal
    assert _event_values(nf, sw.index("notifyListeners(event"), "event", props) == ["statusBarVisibilityChanged"]
    assert _event_values(nf, sw.index("notifyListeners(e."), "e.listenerEvent", props) == ["browserFinished", "browserPageLoaded"]
    assert _event_values(nf, sw.index("notifyListeners(e."), "unknownLocal", props) == []


def test_tauri_same_command_name_in_two_plugins(tmp_path):
    # #97: `get` is the app's own command (generate_handler! in main.rs) and also a command of a `menu` plugin in
    # another folder; `set_icon` is in two plugins. Each command takes the registration closest to its file.
    (tmp_path / "Cargo.toml").write_text('[package]\nname = "app"\nversion = "0.1.0"\n\n[dependencies]\ntauri = "2"\n')
    src = tmp_path / "src"
    (src / "menu").mkdir(parents=True)
    (src / "tray").mkdir()
    (src / "main.rs").write_text(
        "mod menu;\nmod tray;\n\n#[tauri::command]\nfn get() -> i32 { 1 }\n\n"
        "fn main() {\n    tauri::Builder::default().invoke_handler(tauri::generate_handler![get]).run();\n}\n")
    for name in ("menu", "tray"):
        extra = "\n#[tauri::command]\nfn get() -> i32 { 2 }\n" if name == "menu" else ""
        handlers = "get, set_icon" if name == "menu" else "set_icon"
        (src / name / "mod.rs").write_text(
            f"use tauri::plugin::{{Builder, TauriPlugin}};\n\n#[tauri::command]\nfn set_icon() {{}}\n{extra}\n"
            f"pub fn init<R: tauri::Runtime>() -> TauriPlugin<R> {{\n"
            f"    Builder::new(\"{name}\").invoke_handler(tauri::generate_handler![{handlers}]).build()\n}}\n")
    db = tmp_path / "g.db"
    index_project(tmp_path, db, "app")
    st = GraphStore(str(db))
    got = {k: [r["handler"] for r in e["receivers"]] for k, e in endpoints(st).items() if k.startswith("tauri:")}
    assert got == {"tauri:get": ["function:app::get"],
                   "tauri:plugin:menu|get": ["function:app::menu::get"],
                   "tauri:plugin:menu|set_icon": ["function:app::menu::set_icon"],
                   "tauri:plugin:tray|set_icon": ["function:app::tray::set_icon"]}
