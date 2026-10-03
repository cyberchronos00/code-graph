"""Web / native bridges (codegraph/bridges.py): Capacitor plugins, React Native / Expo native modules and Flutter
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
from codegraph import bridges as B  # noqa: E402
from codegraph import query as Q  # noqa: E402
from codegraph.core.model import EDGE_KINDS, PROPAGATING  # noqa: E402
from codegraph.core.store import GraphStore  # noqa: E402
from codegraph.indexer import index_project  # noqa: E402
from codegraph.plugins.dart.plugin import find_dart  # noqa: E402

FX = ROOT / "tests" / "bridge_fixtures"
TS_DEPS = ROOT / "codegraph" / "plugins" / "ts" / "extractor" / "node_modules"
needs_ts = pytest.mark.skipif(not TS_DEPS.exists(), reason="run `npm ci` in codegraph/plugins/ts/extractor")
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
    return subprocess.run([sys.executable, "-m", "codegraph.cli", *args], cwd=ROOT, capture_output=True, text=True)


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
    # registerPlugin<EchoPlugin>('Echo') exported as default and imported: Java @PluginMethod (stub) + Swift CAPPluginMethod
    assert recv(echo) == {"android": "method:com.example.app.EchoPlugin.echo", "ios": "method:EchoPlugin.echo"}
    assert senders(echo) == {"function:src/app.ts#greet"} and echo["senders"][0]["confidence"] == "exact"
    assert echo["receivers"][0]["stub"] is True and st.node("method:com.example.app.EchoPlugin.echo")["lang"] == "java"
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
    from codegraph.core.plugin import GraphBuilder
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
