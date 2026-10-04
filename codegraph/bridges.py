"""Web / native bridges: Capacitor plugins, React Native (and Expo) native modules, Flutter platform channels, and the
process boundaries of desktop apps: Electron IPC / context bridge, Tauri commands.

A bridge call is local message passing between the JS / Dart side of an app and its Kotlin / Java / Swift / ObjC side.
It is modelled with the protocol endpoint model of #31 (epic #29), so the bridge path is

    JS / Dart call site  -SENDS_TO->  endpoint:<protocol>:<namespace>#<method>  -RECEIVED_BY->  native method

  protocol      capacitor | react-native (NativeModules, TurboModules, Expo Modules: attrs.api = expo-modules)
                | flutter (MethodChannel) | flutter-event (EventChannel, no method part: endpoint:flutter-event:<channel>)
                | pigeon (@HostApi / @FlutterApi classes of the Pigeon definition files)
  namespace     Capacitor plugin name, React Native module name, Flutter channel name, Pigeon API class
  direction     to_native (app code sends, native receives) or to_app: a native `invokeMethod` received by a Dart
                `setMethodCallHandler`, a native @FlutterApi call received by the Dart class implementing it
  RECEIVED_BY   one edge per native implementation, in the native file: the platform conventions of platforms.py tag it
                (android/ ios/ macos/ directories, else Kotlin / Java = android, Swift / ObjC = ios), so `--platform ios`
                drops the Android receivers and `impact` / `reaches` cross the bridge in both directions

The send side comes from the language plugins (TypeScript extractor facts, codegraph/plugins/dart/bridges.py); the
receive side is a source-text pass over the native files run here, after the language plugins (Java and ObjC have no
language plugin: their receivers become stub method nodes with attrs.bridge_stub). `finalize` then records per
endpoint which platforms receive it and the checks: missing_on (sent, received on some mobile targets of the project
but not on these), no_receiver (the namespace is implemented in this repo, the method is not), no_sender, external
(nothing in this repo implements the namespace: an npm / pub plugin package).

Process protocols (one app, two processes; receivers carry attrs.process instead of a platform, no missing_on):
  electron-ipc       endpoint:electron-ipc:<channel>; ipcRenderer.invoke / send / sendSync / postMessage and
                     webContents.send -> ipcMain.handle / handleOnce / on / once and ipcRenderer.on / once handlers
  electron-preload   endpoint:electron-preload:<key>#<member>; window.<key>.<member>() in the renderer ->
                     contextBridge.exposeInMainWorld('<key>', {member: ...}) in the preload script
  tauri              endpoint:tauri:<command> (plugin commands: endpoint:tauri:plugin:<name>|<command>, external
                     tauri-plugin-<name> unless the plugin crate is in the repo); invoke('cmd') from @tauri-apps/api ->
                     #[tauri::command] fn in the Rust core; check unregistered: not listed in generate_handler!
no_receiver for these: the other side is in the repo (some channel / command is received) but not this one.
"""
from __future__ import annotations

import bisect
import fnmatch
import json
import os
import re
from collections import defaultdict
from pathlib import Path

PROTOCOLS = {
    "capacitor": "Capacitor plugin method (registerPlugin / Plugins.X -> @CapacitorPlugin / CAPPlugin)",
    "react-native": "React Native native module method (NativeModules / TurboModuleRegistry / requireNativeModule -> "
                    "@ReactMethod / RCT_EXPORT_METHOD / Expo Function)",
    "flutter": "Flutter MethodChannel method (invokeMethod -> setMethodCallHandler)",
    "flutter-event": "Flutter EventChannel stream (receiveBroadcastStream -> setStreamHandler)",
    "pigeon": "Pigeon API method (@HostApi: Dart call -> Kotlin / Swift / Java implementation of the generated "
              "interface; @FlutterApi: native call -> Dart class extending the generated API)",
    "electron-ipc": "Electron IPC channel (ipcRenderer.invoke / send -> ipcMain.handle / on; webContents.send -> "
                    "ipcRenderer.on)",
    "electron-preload": "Electron context bridge member (window.<key>.<member>() -> contextBridge.exposeInMainWorld)",
    "tauri": "Tauri command (invoke('cmd') -> #[tauri::command] fn cmd registered in generate_handler!)",
    "react-native-event": "React Native event (native sendEvent / RCTDeviceEventEmitter.emit / sendEventWithName / Expo "
                          "sendEvent -> NativeEventEmitter / DeviceEventEmitter addListener in JS)",
    "capacitor-event": "Capacitor plugin event (native notifyListeners('evt') -> Plugin.addListener('evt') in JS)",
    "cordova": "Cordova plugin action (cordova.exec(ok, fail, 'Service', 'action') -> CordovaPlugin.execute / CDVPlugin "
               "method; services from plugin.xml / config.xml <feature>)",
}
# native -> app events: the JS listener is the receiver; no_sender only where the repo sends some event natively
EVENT_PROTOCOLS = ("react-native-event", "capacitor-event")
# protocols between processes of one app (no per-platform receivers)
PROCESS_PROTOCOLS = ("electron-ipc", "electron-preload", "tauri")
TRANSPORT = {"electron-ipc": "ipc", "tauri": "ipc", "electron-preload": "local"}
MOBILE = ("android", "ios", "macos")
APP_LANGS = ("dart", "ts")          # the app side of a bridge (receivers there: a native -> app call)
# Capacitor Plugin / CAPPlugin base-class methods: handled by the bridge on every platform unless a plugin overrides them
CAP_BASE_METHODS = {"checkPermissions", "requestPermissions", "addListener", "removeAllListeners", "removeListener"}
# NativeEventEmitter plumbing (RCTEventEmitter implements it on iOS) and the TurboModule constants getter
RN_BASE_METHODS = {"addListener", "removeListeners", "getConstants"}
BASE_METHODS = {"capacitor": CAP_BASE_METHODS, "react-native": RN_BASE_METHODS}


def _flutter_folders(root: Path, rel: str | None, cache: dict) -> set | None:
    """Platform folders (android/, ios/, macos/) next to the nearest pubspec.yaml above a Dart file."""
    if not rel:
        return None
    d = Path(rel).parent
    while True:
        key = ("pubspec", str(d))
        if key not in cache:
            cache[key] = ({p for p in ("android", "ios", "macos") if (root / d / p).is_dir()}
                          if (root / d / "pubspec.yaml").is_file() else None)
        if cache[key] is not None:
            return cache[key]
        if d == d.parent or str(d) in ("", "."):
            return None
        d = d.parent


def _codegen_platforms(root: Path, rel: str, cache: dict) -> list[str] | None:
    """platforms of the nearest package.json's React Native codegenConfig (a module declared for android only)."""
    d = Path(rel).parent
    while True:
        key = str(d)
        if key not in cache:
            cache[key] = None
            pj = root / d / "package.json"
            if pj.is_file():
                try:
                    cfg = json.loads(pj.read_text(encoding="utf-8", errors="replace")).get("codegenConfig") or {}
                    plats = cfg.get("platforms") if isinstance(cfg, dict) else None
                    cache[key] = [p for p in plats if isinstance(p, str)] if isinstance(plats, list) else []
                except (OSError, ValueError, AttributeError):
                    cache[key] = []
        if cache[key] is not None:
            return cache[key] or None
        if d == d.parent or str(d) in ("", "."):
            return None
        d = d.parent
NATIVE_EXTS = (".kt", ".java", ".swift", ".m", ".mm")


# ------------------------------------------------------------------ protocol helpers (the #31 builder helper shape)
def endpoint_key(protocol: str, namespace: str, method: str | None = None) -> str:
    """Key of endpoint:<protocol>:<name>, name = <namespace>#<method> (or the namespace alone for streams)."""
    return f"{protocol}:{namespace}#{method}" if method else f"{protocol}:{namespace}"


def _endpoint(builder, protocol: str, namespace: str, method: str | None) -> str:
    key = endpoint_key(protocol, namespace, method)
    name = key.split(":", 1)[1]
    return builder.add_node("endpoint", key, name, fqn=name,
                            attrs={"protocol": protocol, "transport": TRANSPORT.get(protocol, "local"), "namespace": namespace,
                                   "method": method})


def protocol_send(builder, protocol: str, namespace: str, method: str | None, src: str, file: str | None,
                  line: int | None, confidence: str, test: bool = False, **attrs) -> str:
    """Code `src` sends to an endpoint (SENDS_TO; test code: TEST_CALLS with orig SENDS_TO)."""
    nid = _endpoint(builder, protocol, namespace, method)
    n = builder.nodes[nid]
    if test:
        n.attrs.setdefault("test_only", True)
    else:
        n.attrs["test_only"] = False
    a = {k: v for k, v in attrs.items() if v not in (None, [], "")}
    if test:
        builder.add_edge(src, nid, "TEST_CALLS", file=file, line=line, confidence=confidence, orig="SENDS_TO", **a)
    else:
        builder.add_edge(src, nid, "SENDS_TO", file=file, line=line, confidence=confidence, role="invoke", **a)
    return nid


def protocol_receive(builder, protocol: str, namespace: str, method: str | None, handler: str, file: str | None,
                     line: int | None, confidence: str, **attrs) -> str:
    """An endpoint is handled by `handler` (RECEIVED_BY, endpoint -> handler)."""
    nid = _endpoint(builder, protocol, namespace, method)
    builder.add_edge(nid, handler, "RECEIVED_BY", file=file, line=line, confidence=confidence,
                     **{k: v for k, v in attrs.items() if v not in (None, [], "")})
    return nid


# ------------------------------------------------------------------ source text helpers
_TOKEN = re.compile(r'"(?:\\.|[^"\\\n])*"|//[^\n]*|/\*.*?\*/', re.S)


def strip_comments(src: str) -> str:
    """Comments blanked (newlines kept, so offsets and line numbers stay), string literals kept."""
    def rep(m):
        t = m.group(0)
        if t.startswith('"'):
            return t
        return re.sub(r"[^\n]", " ", t)
    return _TOKEN.sub(rep, src)


def _blank_strings(src: str) -> str:
    return re.sub(r'"(?:\\.|[^"\\\n])*"', lambda m: '"' + " " * (len(m.group(0)) - 2) + '"', src)


class Text:
    def __init__(self, src: str):
        self.src = strip_comments(src)
        self.code = _blank_strings(self.src)        # for brace matching: braces inside strings do not count
        self.nl = [i for i, c in enumerate(self.src) if c == "\n"]

    def line(self, pos: int) -> int:
        return bisect.bisect_right(self.nl, pos - 1) + 1

    def match(self, pos: int, open_: str = "{", close: str = "}") -> int:
        """Offset of the bracket closing the one at pos (or the first `open_` at / after pos); len(src) if unbalanced."""
        i = self.code.find(open_, pos)
        if i < 0:
            return len(self.code)
        depth = 0
        for j in range(i, len(self.code)):
            c = self.code[j]
            if c == open_:
                depth += 1
            elif c == close:
                depth -= 1
                if depth == 0:
                    return j
        return len(self.code)

    def args(self, open_pos: int) -> list[tuple[int, str]]:
        """Top-level comma-separated arguments of the call whose `(` is at open_pos: [(offset, text)]."""
        end = self.match(open_pos, "(", ")")
        out, depth, start = [], 0, open_pos + 1
        for j in range(open_pos + 1, end):
            c = self.code[j]
            if c in "([{":
                depth += 1
            elif c in ")]}":
                depth -= 1
            elif c == "," and depth == 0:
                out.append((start, self.src[start:j].strip()))
                start = j + 1
        tail = self.src[start:end].strip()
        if tail:
            out.append((start, tail))
        return out


CLASS_RE = re.compile(r"\b(?:class|object|struct|actor)\s+(\w+)\s*(?:<[^{;()]*?>)?\s*"
                      r"(?:\((?:[^()]|\([^()]*\))*\))?[^{;=]*\{")     # a primary constructor may hold `= default`
OBJC_IMPL_RE = re.compile(r"@implementation\s+(\w+)")
CONST_RE = re.compile(r"\b(?:val|var|let|const\s+val|static\s+let|static\s+var|(?:public\s+|private\s+|protected\s+)?"
                      r"(?:static\s+)?final\s+String|String)\s+(\w+)\s*(?::\s*String\??)?\s*=\s*\"([^\"\\\n]*)\"")
OBJC_CONST_RE = re.compile(r"NSString\s*\*\s*(?:const\s+)?(\w+)\s*=\s*@\"([^\"\n]*)\"|#define\s+(\w+)\s+@\"([^\"\n]*)\"")
PACKAGE_RE = re.compile(r"^\s*package\s+([\w.]+)", re.M)


class NativeFile:
    def __init__(self, rel: str, src: str):
        self.rel, self.t = rel, Text(src)
        self.ext = os.path.splitext(rel)[1]
        self.lang = {".kt": "kotlin", ".java": "java", ".swift": "swift", ".m": "objc", ".mm": "objc"}[self.ext]
        m = PACKAGE_RE.search(self.t.src)
        self.package = m.group(1) if m and self.lang in ("kotlin", "java") else None
        self.classes: list[tuple[str, int, int]] = []          # (name, start, end) offsets
        for m in CLASS_RE.finditer(self.t.code):
            self.classes.append((m.group(1), m.start(), self.t.match(m.end() - 1)))
        if self.lang == "objc":
            for m in OBJC_IMPL_RE.finditer(self.t.code):
                e = self.t.code.find("@end", m.end())
                self.classes.append((m.group(1), m.start(), e if e >= 0 else len(self.t.code)))
        self.consts = {}
        for m in CONST_RE.finditer(self.t.src):
            self.consts.setdefault(m.group(1), m.group(2))
        for m in OBJC_CONST_RE.finditer(self.t.src):
            if m.group(1):
                self.consts.setdefault(m.group(1), m.group(2))
            else:
                self.consts.setdefault(m.group(3), m.group(4))

    def class_at(self, pos: int) -> tuple[str, int, int] | None:
        best = None
        for c in self.classes:
            if c[1] <= pos <= c[2] and (best is None or c[1] > best[1]):
                best = c
        return best

    def class_named(self, name: str):
        return next((c for c in self.classes if c[0] == name), None)


TRIGGER = re.compile(r"CapacitorPlugin|CAPPlugin|CAP_PLUGIN|ReactMethod|RCT_(?:EXPORT|EXTERN|REMAP)|ReactContextBaseJavaModule|"
                     r"BaseJavaModule|Native\w+Spec\b|ModuleDefinition|MethodChannel|EventChannel|MethodCallHandler|"
                     r"StreamHandler|isEqualToString:\s*call\.method|call\.method|@objc\s*\(|"
                     r"RCTEventEmitter|RCTDeviceEventEmitter|DeviceEventManagerModule|sendEventWithName|sendEvent\s*\(|emitDeviceEvent|"
                     r"notifyListeners|CordovaPlugin|CDVPlugin|CDVInvokedUrlCommand")


# ------------------------------------------------------------------ receivers found in native source text
class Receiver:
    __slots__ = ("protocol", "namespace", "method", "file", "pos", "fn", "cls", "conf", "via", "api", "lookup")

    def __init__(self, protocol, namespace, method, nf: NativeFile, pos, fn=None, cls=None, conf="resolved", via=None,
                 api=None, lookup=None):
        self.protocol, self.namespace, self.method = protocol, namespace, method
        self.file, self.pos, self.fn, self.cls, self.conf, self.via, self.api = nf, pos, fn, cls, conf, via, api
        self.lookup = lookup          # (swift / objc class name, method name): implemented in another file


ANN_TAIL = r"\s*(?:@\w+(?:\.\w+)*(?:\([^)]*\))?\s*)*(?:(?:public|private|protected|internal|static|final|override|open|" \
           r"suspend|synchronized|native)\s+)*(?:fun\s+(?:<[^>]*>\s*)?(\w+)|[\w<>\[\],.?]+(?:\s*<[^>]*>)?\s+(\w+))\s*\("
CAP_PLUGIN_ANN = re.compile(r"@CapacitorPlugin\b\s*(\()?")
PLUGIN_METHOD = re.compile(r"@PluginMethod\b(?:\s*\([^)]*\))?" + ANN_TAIL)
REACT_METHOD = re.compile(r"@ReactMethod\b(?:\s*\([^)]*\))?" + ANN_TAIL)
JAVA_CLASS = re.compile(r"\bclass\s+(\w+)\s*(?:\([^)]*\))?\s*(?::|extends)\s*([^{]*)\{")
GET_NAME = re.compile(r"\bgetName\s*\(\s*\)\s*(?::\s*String\s*)?(?:=\s*|\{\s*return\s+)(\"([^\"]*)\"|([\w.]+))")
OVERRIDE_FUN = re.compile(r"(?:\boverride\s+fun\s+(\w+)\s*\(|@Override\s+(?:public\s+)?(?:synchronized\s+)?[\w<>\[\],.?]+\s+(\w+)\s*\()")
SPEC_SKIP = {"getName", "getConstants", "getTypedExportedConstants", "initialize", "invalidate", "onCatalystInstanceDestroy",
             "canOverrideExistingModule", "hasConstants", "getExportedConstants", "toString", "equals", "hashCode",
             "onHostResume", "onHostPause", "onHostDestroy", "onActivityResult", "onNewIntent", "createNativeModules",
             "createViewManagers", "getReactModuleInfoProvider", "getModule"}
SWIFT_CLASS = re.compile(r"(?:@objc\s*\(\s*(\w+)\s*\)\s*)?(?:(?:public|open|final|internal|private)\s+)*class\s+(\w+)\s*:\s*([^{]*)\{")
CAP_METHOD_NAME = re.compile(r"CAPPluginMethod\s*\(\s*name:\s*\"(\w+)\"|CAPPluginMethod\s*\(\s*\"(\w+)\"")
JS_NAME = re.compile(r"\bjsName\s*=\s*\"([^\"]+)\"")
OBJC_FUNC = re.compile(r"@objc(?:\s*\([^)]*\))?\s+(?:(?:public|open|internal|private|final|override|dynamic)\s+)*func\s+(\w+)\s*\(")
SWIFT_FUNC = re.compile(r"\bfunc\s+(\w+)\s*(?:<[^>]*>)?\s*\(")
RCT_MODULE = re.compile(r"\bRCT_EXPORT_MODULE\s*\(\s*(\w*)\s*\)")
RCT_EXPORT = re.compile(r"\bRCT_(?:EXPORT_METHOD|EXPORT_BLOCKING_SYNCHRONOUS_METHOD)\s*\(\s*(\w+)|"
                        r"\bRCT_(?:REMAP_METHOD|REMAP_BLOCKING_SYNCHRONOUS_METHOD)\s*\(\s*(\w+)\s*,")
RCT_EXTERN_MODULE = re.compile(r"\bRCT_EXTERN_MODULE\s*\(\s*(\w+)\s*,|\bRCT_EXTERN_REMAP_MODULE\s*\(\s*(\w*)\s*,\s*(\w+)\s*,")
RCT_EXTERN_METHOD = re.compile(r"\bRCT_EXTERN_(?:_BLOCKING_SYNCHRONOUS_)?METHOD\s*\(\s*(\w+)")
EXPO_NAME = re.compile(r"\bName\s*\(\s*\"([^\"]+)\"\s*\)")
EXPO_FN = re.compile(r"\b(?:AsyncFunction|Function)\s*\(\s*\"(\w+)\"")
CHANNEL_NEW = re.compile(r"(?<![\w.])(?:new\s+)?(?:io\.flutter\.plugin\.common\.)?(Method|Event|OptionalMethod)Channel\s*\(")
SWIFT_CHANNEL_NEW = re.compile(r"\bFlutter(Method|Event)Channel\s*\(\s*name:\s*")
OBJC_CHANNEL_NEW = re.compile(r"\[\s*Flutter(Method|Event)Channel\s+(?:methodChannel|eventChannel)WithName:\s*(@\"[^\"]*\"|\w+)")
METHOD_EQ = re.compile(r"\b\w+\.method\s*==\s*\"([^\"]+)\"|\"([^\"]+)\"\s*==\s*\w+\.method\b|"
                       r"\"([^\"]+)\"\.equals\s*\(\s*\w+\.method\s*\)|\b\w+\.method\.equals\s*\(\s*\"([^\"]+)\"\s*\)|"
                       r"\[\s*@\"([^\"]+)\"\s+isEqualToString:\s*\w+\.method\s*\]|\[\s*\w+\.method\s+isEqualToString:\s*@\"([^\"]+)\"\s*\]")
SWITCH_ON = re.compile(r"\b(?:when|switch)\s*\(?\s*\w+\.method\s*\)?\s*\{")
CASE_STR = re.compile(r"(?:\bcase\s+((?:\"[^\"]+\"\s*,\s*)*\"[^\"]+\")\s*:|^\s*((?:\"[^\"]+\"\s*,\s*)*\"[^\"]+\")\s*->)", re.M)


def _str_value(nf: NativeFile, expr: str, consts: dict) -> str | None:
    e = expr.strip()
    if e.startswith("name:"):
        e = e[5:].strip()
    m = re.fullmatch(r'@?"([^"\\\n]*)"', e)
    if m:
        return m.group(1)
    if re.fullmatch(r"[\w.]+", e):
        last = e.split(".")[-1]
        if last in nf.consts:
            return nf.consts[last]
        if e in consts:
            return consts[e]
        if last in consts:
            return consts[last]
    return None


def scan_capacitor(nf: NativeFile, out: list, objc_plugins: dict) -> None:
    t = nf.t
    if nf.lang in ("kotlin", "java"):
        for m in CAP_PLUGIN_ANN.finditer(t.code):
            name = None
            if m.group(1):
                end = t.match(m.end() - 1, "(", ")")
                nm = re.search(r"\bname\s*=\s*\"([^\"]+)\"", t.src[m.end():end])
                name = nm.group(1) if nm else None
            cm = re.compile(r"\bclass\s+(\w+)").search(t.code, m.end())
            if not cm:
                continue
            cls = nf.class_named(cm.group(1))
            ns = name or cm.group(1)
            lo, hi = (cls[1], cls[2]) if cls else (cm.start(), len(t.code))
            for pm in PLUGIN_METHOD.finditer(t.code, lo, hi):
                fn = pm.group(1) or pm.group(2)
                out.append(Receiver("capacitor", ns, fn, nf, pm.start(pm.lastindex), fn=fn, cls=cm.group(1), conf="exact",
                                    via="@PluginMethod"))
    elif nf.lang == "swift":
        for m in SWIFT_CLASS.finditer(t.code):
            bases = m.group(3)
            if not re.search(r"\bCAPPlugin\b|\bCAPBridgedPlugin\b", bases):
                continue
            cls = m.group(2)
            end = t.match(m.end() - 1)
            body = t.src[m.start():end]
            jm = JS_NAME.search(body)
            listed = [a or b for a, b in CAP_METHOD_NAME.findall(body)]
            objc_name = m.group(1) or cls
            reg = objc_plugins.get(objc_name) or objc_plugins.get(cls)
            ns = jm.group(1) if jm else (reg[0] if reg else None)
            if not ns:
                continue
            names = set(listed) | set(reg[1] if reg else ())
            funcs = {f.group(1): f.start(1) for f in OBJC_FUNC.finditer(t.code, m.start(), end)}
            for fn in sorted(names) if names else sorted(funcs):
                if fn in funcs:
                    out.append(Receiver("capacitor", ns, fn, nf, funcs[fn], fn=fn, cls=cls, conf="exact",
                                        via="CAPPluginMethod" if fn in listed else "CAP_PLUGIN_METHOD" if names else "@objc func"))


def scan_objc_capacitor(nf: NativeFile, objc_plugins: dict) -> None:
    """CAP_PLUGIN(Class, "JsName", CAP_PLUGIN_METHOD(m, ...); ...) registrations (old-style Capacitor iOS plugins)."""
    t = nf.t
    for m in re.finditer(r"\bCAP_PLUGIN\s*\(", t.code):
        args = t.args(m.end() - 1)
        if len(args) < 2:
            continue
        cls, js = args[0][1], re.fullmatch(r'"([^"]+)"', args[1][1])
        if not js:
            continue
        rest = t.src[args[1][0]:t.match(m.end() - 1, "(", ")")]
        methods = [x.group(1) for x in re.finditer(r"CAP_PLUGIN_METHOD\s*\(\s*(\w+)", rest)]
        objc_plugins[cls] = (js.group(1), methods, nf, m.start())


def scan_react_native(nf: NativeFile, out: list, spec_names: dict, extern: dict) -> None:
    t = nf.t
    if nf.lang in ("kotlin", "java"):
        for m in JAVA_CLASS.finditer(t.code):
            bases = m.group(2)
            spec = re.search(r"\b(Native\w+Spec)\b", bases)
            if not (spec or re.search(r"\b(?:ReactContextBaseJavaModule|BaseJavaModule)\b", bases)):
                continue
            cls = m.group(1)
            end = t.match(m.end() - 1)
            body = t.src[m.start():end]
            ns = None
            gm = GET_NAME.search(body)
            if gm:
                ns = gm.group(2) if gm.group(2) is not None else _str_value(nf, gm.group(3), _GLOBAL_CONSTS)
            if not ns and spec:
                ns = spec_names.get(spec.group(1)) or spec.group(1)[len("Native"):-len("Spec")]
            if not ns:
                ns = nf.consts.get("NAME")
            if not ns:
                continue
            seen = set()
            for rm in REACT_METHOD.finditer(t.code, m.start(), end):
                fn = rm.group(1) or rm.group(2)
                seen.add(fn)
                out.append(Receiver("react-native", ns, fn, nf, rm.start(rm.lastindex), fn=fn, cls=cls, conf="exact",
                                    via="@ReactMethod"))
            if spec:
                for om in OVERRIDE_FUN.finditer(t.code, m.start(), end):
                    fn = om.group(1) or om.group(2)
                    if fn in seen or fn in SPEC_SKIP:
                        continue
                    seen.add(fn)
                    out.append(Receiver("react-native", ns, fn, nf, om.start(om.lastindex), fn=fn, cls=cls, conf="exact",
                                        via=spec.group(1)))
    elif nf.lang == "objc":
        for m in RCT_MODULE.finditer(t.code):
            c = nf.class_at(m.start())
            cls = c[0] if c else None
            ns = m.group(1) or (re.sub(r"^RCT", "", cls) if cls else None)
            if not ns:
                continue
            lo, hi = (c[1], c[2]) if c else (0, len(t.code))
            for em in RCT_EXPORT.finditer(t.code, lo, hi):
                fn = em.group(1) or em.group(2)
                out.append(Receiver("react-native", ns, fn, nf, em.start(), fn=fn, cls=cls, conf="exact",
                                    via="RCT_EXPORT_METHOD"))
        for m in RCT_EXTERN_MODULE.finditer(t.code):
            cls = m.group(1) or m.group(3)
            ns = m.group(2) or cls
            e = t.code.find("@end", m.end())
            hi = e if e >= 0 else len(t.code)
            for em in RCT_EXTERN_METHOD.finditer(t.code, m.end(), hi):
                extern.setdefault(cls, []).append((ns, em.group(1), nf, em.start()))


def scan_expo(nf: NativeFile, out: list) -> None:
    t = nf.t
    if nf.lang not in ("kotlin", "swift") or "ModuleDefinition" not in t.code:
        return
    for m in re.finditer(r"\bdefinition\s*\(\s*\)", t.code):
        end = t.match(m.end())
        body_lo = t.code.find("{", m.end())
        nm = EXPO_NAME.search(t.src, m.end(), end)
        c = nf.class_at(m.start())
        ns = nm.group(1) if nm else (c[0] if c else None)
        if not ns or body_lo < 0:
            continue
        for fm in EXPO_FN.finditer(t.src, body_lo, end):
            out.append(Receiver("react-native", ns, fm.group(1), nf, m.start(), fn="definition", cls=c[0] if c else None,
                                conf="exact", via="Expo " + fm.group(0).split("(")[0].strip(), api="expo-modules"))


def scan_flutter(nf: NativeFile, out: list, handler_classes: dict, pending_sites: list) -> None:
    """MethodChannel / EventChannel registrations with their handler regions, and `call.method` sites."""
    t = nf.t
    regs = []        # (kind 'method'|'event', name, pos, region (lo, hi) or None, handler class name or None)
    news = []
    if nf.lang in ("kotlin", "java"):
        for m in CHANNEL_NEW.finditer(t.code):
            args = t.args(m.end() - 1)
            if len(args) >= 2:
                news.append(("event" if m.group(1) == "Event" else "method", args[1][1], m.start(), t.match(m.end() - 1, "(", ")")))
    elif nf.lang == "swift":
        for m in SWIFT_CHANNEL_NEW.finditer(t.code):
            args = t.args(t.code.rfind("(", 0, m.end()))
            if args:
                news.append(("event" if m.group(1) == "Event" else "method", args[0][1], m.start(),
                             t.match(t.code.rfind("(", 0, m.end()), "(", ")")))
    else:
        for m in OBJC_CHANNEL_NEW.finditer(t.src):
            news.append(("event" if m.group(1) == "Event" else "method", m.group(2), m.start(), t.match(m.start(), "[", "]")))
    for kind, expr, pos, close in news:
        name = _str_value(nf, expr, _GLOBAL_CONSTS)
        if not name:
            continue
        setter = "setStreamHandler" if kind == "event" else "setMethodCallHandler"
        region, hcls = None, None
        # chained: MethodChannel(...).setMethodCallHandler(...) / held in a variable: x = MethodChannel(...); x.set...(...)
        after = t.code[close + 1:close + 200]
        sm = re.match(r"\s*\??\.\s*" + setter + r"\b", after)
        hpos = close + 1 + sm.end() if sm else None
        if hpos is None:
            head = t.code[max(0, pos - 160):pos]
            vm = re.search(r"(?:\b(?:val|var|let|final\s+\w+|\w+)\s+)?(?:self\.|this\.)?(\w+)\s*(?::\s*[\w.<>?]+\s*)?=\s*(?:new\s+)?$",
                           head)
            if vm:
                hm = re.compile(r"\b" + re.escape(vm.group(1)) + r"\s*[?!]?\s*\.\s*" + setter + r"\b").search(t.code, close)
                if hm:
                    hpos = hm.end()
        if hpos is not None:
            rest = t.code[hpos:hpos + 400]
            lm = re.match(r"\s*\(?\s*(\{)", rest)            # trailing lambda / closure
            cm = re.match(r"\s*\(\s*(?:new\s+)?(\w+)\s*(?:\(|\))", rest)
            if lm:
                lo = hpos + lm.start(1)
                region = (lo, t.match(lo))
            elif cm and cm.group(1) in ("this", "self"):
                c = nf.class_at(pos)
                region = (c[1], c[2]) if c else None
            elif cm and cm.group(1)[:1].isupper():
                hcls = cm.group(1)
                c = nf.class_named(hcls)
                region = (c[1], c[2]) if c else None
        regs.append((kind, name, pos, region, hcls))
    sites = []
    for m in METHOD_EQ.finditer(t.src):
        sites.append((next(g for g in m.groups() if g is not None), m.start()))
    for m in SWITCH_ON.finditer(t.code):
        end = t.match(m.end() - 1)
        for cm in CASE_STR.finditer(t.src, m.end(), end):
            for s in re.findall(r'"([^"]+)"', cm.group(1) or cm.group(2)):
                sites.append((s, cm.start()))
    mregs = [r for r in regs if r[0] == "method"]
    for kind, name, pos, region, hcls in regs:
        if hcls:
            handler_classes.setdefault(hcls, []).append((kind, name, nf, pos))
        if kind == "event":
            # the stream handler: onListen of the handler class / region, else the registering function
            if region:
                lm = re.compile(r"\bonListen\b").search(t.code, region[0], region[1])
                out.append(Receiver("flutter-event", name, None, nf, lm.start() if lm else pos, conf="resolved",
                                    via="setStreamHandler"))
            elif hcls:
                pending_sites.append(("event", name, hcls, nf, pos))
            else:
                out.append(Receiver("flutter-event", name, None, nf, pos, conf="heuristic", via="EventChannel"))
    for meth, spos in sites:
        owners = [r for r in mregs if r[3] and r[3][0] <= spos <= r[3][1]]
        if owners:
            for r in owners[-1:]:
                out.append(Receiver("flutter", r[1], meth, nf, spos, conf="resolved", via="setMethodCallHandler"))
            continue
        c = nf.class_at(spos)
        if c:
            pending_sites.append(("method", meth, c[0], nf, spos))       # a handler class registered elsewhere
        if len(mregs) == 1 and not (c and any(r[4] == c[0] for r in mregs)):
            out.append(Receiver("flutter", mregs[0][1], meth, nf, spos, conf="heuristic", via="call.method"))
        elif len(mregs) > 1:
            for r in mregs:
                out.append(Receiver("flutter", r[1], meth, nf, spos, conf="heuristic", via="call.method"))


_GLOBAL_CONSTS: dict[str, str] = {}
# string-valued enum cases (#95): Swift `enum Event: String { case a, b = "x" }` -> `Event.a.rawValue`, Kotlin
# `enum class Events(val event: String) { SAVED("Saved") }` -> `Events.SAVED.event`
SWIFT_STR_ENUM = re.compile(r"\benum\s+(\w+)\s*:\s*String\b[^{]*\{")
KT_STR_ENUM = re.compile(r"\benum\s+class\s+(\w+)\s*\(\s*(?:(?:private|internal|public)\s+)?val\s+(\w+)\s*:\s*String\s*\)[^{]*\{")


def _enum_consts(rel: str, src: str) -> dict[str, str]:
    out: dict[str, str] = {}
    if rel.endswith(".swift") and "enum" in src:
        for m in SWIFT_STR_ENUM.finditer(src):
            depth, i = 1, m.end()
            while i < len(src) and depth:
                depth += {"{": 1, "}": -1}.get(src[i], 0)
                i += 1
            body = src[m.end():i - 1]
            for cm in re.finditer(r"^\s*case\s+([^\n{(]+)$", body, re.M):
                for part in cm.group(1).split(","):
                    pm = re.fullmatch(r'\s*(\w+)\s*(?:=\s*"([^"\\\n]*)")?\s*(?://.*)?', part)
                    if pm:
                        out[f"{m.group(1)}.{pm.group(1)}.rawValue"] = pm.group(2) if pm.group(2) is not None else pm.group(1)
    elif rel.endswith(".kt") and "enum" in src:
        for m in KT_STR_ENUM.finditer(src):
            body = src[m.end():src.find("}", m.end())]
            for em in re.finditer(r'\b([A-Z_][A-Z0-9_]*)\s*\(\s*"([^"\\\n]*)"\s*\)', body):
                out[f"{m.group(1)}.{em.group(1)}.{m.group(2)}"] = em.group(2)
    return out

# ------------------------------------------------------------------ Flutter MethodChannel: native -> Dart
NATIVE_INVOKE = re.compile(r"(?:\b(?:self|this)\s*\.\s*)?(\w+)\s*[?!]*\s*\.\s*invokeMethod\s*(?:<[^>]*>)?\s*\(\s*")
OBJC_INVOKE = re.compile(r"\[\s*(?:self\.|_)?(\w+)\s+invokeMethod:\s*")


def flutter_native_sends(builder, by_file, nfs: list) -> int:
    """`channel.invokeMethod("m", args)` in Kotlin / Java / Swift / ObjC on a MethodChannel whose name is known
    (the variable the channel was assigned to, or the file's only channel) -> SENDS_TO endpoint:flutter:<channel>#m
    from the enclosing native method; the Dart `setMethodCallHandler` that tests `call.method == 'm'` receives it."""
    n = 0
    for nf in nfs:
        t = nf.t
        if "invokeMethod" not in t.code:
            continue
        chans = {}                      # variable -> channel name
        names = []
        if nf.lang in ("kotlin", "java"):
            news = [(m.start(), t.args(m.end() - 1)) for m in CHANNEL_NEW.finditer(t.code) if m.group(1) != "Event"]
            news = [(p, a[1][1]) for p, a in news if len(a) >= 2]
        elif nf.lang == "swift":
            news = []
            for m in SWIFT_CHANNEL_NEW.finditer(t.code):
                if m.group(1) == "Event":
                    continue
                a = t.args(t.code.rfind("(", 0, m.end()))
                if a:
                    news.append((m.start(), a[0][1]))
        else:
            news = [(m.start(), m.group(2)) for m in OBJC_CHANNEL_NEW.finditer(t.src) if m.group(1) != "Event"]
        for pos, expr in news:
            name = _str_value(nf, expr, _GLOBAL_CONSTS)
            if not name:
                continue
            names.append(name)
            head = t.code[max(0, pos - 160):pos]
            vm = re.search(r"(?:self\.|this\.|_)?(\w+)\s*(?::\s*[\w.<>?]+\s*)?=\s*(?:new\s+)?(?:\[\s*)?$", head)
            if vm:
                chans.setdefault(vm.group(1), name)
        if not names:
            continue
        rx = OBJC_INVOKE if nf.lang == "objc" else NATIVE_INVOKE
        for m in rx.finditer(t.code):
            var = m.group(1).lstrip("_")
            name = chans.get(var) or chans.get(m.group(1)) or (names[0] if len(set(names)) == 1 else None)
            if not name:
                continue
            sm = re.match(r'@?"([^"\n]+)"', t.src[m.end():m.end() + 200])
            if not sm:
                continue
            conf = "resolved" if (var in chans or m.group(1) in chans) else "heuristic"
            line = t.line(m.start())
            hid = _handler(builder, by_file, Receiver("flutter", name, sm.group(1), nf, m.start()), nf, line)
            if hid is None:
                continue
            plat, _why = _platform_of(nf.rel, nf.lang)
            protocol_send(builder, "flutter", name, sm.group(1), hid, nf.rel, line, conf, via="invokeMethod (native)",
                          platform=plat, direction="to_app")
            n += 1
    return n


# ------------------------------------------------------------------ native -> JS events (#61)
RN_EMIT = re.compile(r"\.\s*emit\s*\(\s*")
RN_SEND_EVENT = re.compile(r"\bsendEvent\s*\(\s*(?:withName:\s*)?")
RN_EMIT_DEVICE = re.compile(r"\.\s*emitDeviceEvent\s*\(\s*")
JVM_FUN = re.compile(r"\bfun\s+(?:<[^>]*>\s*)?(\w+)\s*\(|\b(\w+)\s*\([^()]*\)\s*(?:throws\s+[\w.,\s]+)?\{")
OBJC_SEND_EVENT = re.compile(r"\[\s*(?:self|_?\w+)\s+sendEventWithName:\s*")
CAP_NOTIFY = re.compile(r"\bnotifyListeners\s*\(\s*")
OBJC_CAP_NOTIFY = re.compile(r"\[\s*(?:self|_?\w+)\s+notifyListeners:\s*")
RN_EMITTER_HINT = re.compile(r"RCTDeviceEventEmitter|DeviceEventManagerModule|RCTNativeAppEventEmitter|RCTEventEmitter|"
                             r"ModuleDefinition|ReactContext|ReactApplicationContext")


def _first_value(nf: NativeFile, text: str, after: int, args: bool) -> tuple[str | None, str]:
    """The event name at `after` (the first call argument, or an ObjC message argument): (value, raw text)."""
    t = nf.t
    if args:
        a = t.args(after - 1) if after > 0 and t.code[after - 1] == "(" else t.args(t.code.rfind("(", 0, after))
        if not a:
            return None, ""
        parts = [x[1].strip() for x in a]
        # sendEvent(reactContext, "evt", params): the first string-valued argument among the first two
        for raw in parts[:2]:
            v = _str_value(nf, raw.split(":", 1)[1] if re.match(r"^\w+:", raw) else raw, _GLOBAL_CONSTS)
            if v:
                return v, raw
        return None, parts[0] if parts else ""
    m = re.match(r'(@?"[^"\n]*"|[\w.]+)', t.src[after:after + 200])
    if not m:
        return None, ""
    return _str_value(nf, m.group(1), _GLOBAL_CONSTS), m.group(1)


SWIFT_STR_PROP = re.compile(r"\bvar\s+(\w+)\s*:\s*String\s*\{")


def _str_props(nf: NativeFile) -> dict:
    """Swift computed `var name: String { switch self { case .a: return "x" ... } }` whose every return is a string
    literal -> name -> [values] (`event.listenerEvent` is one of them)."""
    if nf.lang != "swift" or "String" not in nf.t.code:
        return {}
    out, t = {}, nf.t
    for m in SWIFT_STR_PROP.finditer(t.code):
        ob = m.end() - 1
        body = t.src[ob:t.match(ob) + 1]
        lits = re.findall(r'\breturn\s+"([^"\\\n]*)"', body) + re.findall(r'\bcase\s+[^:\n]+:\s*"([^"\\\n]*)"', body)
        if lits and len(re.findall(r"\breturn\b", t.code[ob:t.match(ob) + 1])) == len(re.findall(r'\breturn\s+"', body)):
            out[m.group(1)] = sorted(set(lits))
    return out


def _event_values(nf: NativeFile, pos: int, raw: str, props: dict) -> list[str]:
    """Event names a non-literal argument stands for: `self?.name` (a constant), a local `let event = self?.name` /
    `val event = NAME` declared before the call, or `event.listenerEvent` (a Swift computed string property)."""
    e = re.sub(r"[?!](?=\.)", "", (raw or "").strip())
    if e.startswith("name:"):
        e = e[5:].strip()
    if not e:
        return []
    v = _str_value(nf, e, _GLOBAL_CONSTS)
    if v:
        return [v]
    if re.fullmatch(r"[a-z]\w*", e):
        src = nf.t.src[max(0, pos - 3000):pos]
        last = None
        for m in re.finditer(r"\b(?:let|var|val|final\s+String|String)\s+" + re.escape(e) +
                             r"\s*(?::\s*String\??)?\s*=\s*([^\n,;{]+)", src):
            last = m
        if last:
            x = re.sub(r"[?!](?=\.)", "", re.split(r"\s+else\b", last.group(1))[0].strip())
            v = _str_value(nf, x, _GLOBAL_CONSTS)
            if v:
                return [v]
            if "." in x and x.split(".")[-1] in props:
                return props[x.split(".")[-1]]
        return []
    if re.fullmatch(r"[\w.]+", e) and "." in e and e.split(".")[-1] in props:
        return props[e.split(".")[-1]]
    return []


def event_native_sends(builder, by_file, nfs: list, recs: list, dynamic: list, jvm_other: list = ()) -> dict:
    """Native code emitting an event to JS -> SENDS_TO endpoint (direction to_app) from the enclosing method:
    React Native `RCTDeviceEventEmitter.emit("evt", ...)`, `sendEvent(ctx, "evt", ...)`, ObjC `sendEventWithName:`,
    Swift `sendEvent(withName:)`, Expo `sendEvent("evt", ...)`; Capacitor `notifyListeners("evt", data)` in a plugin
    class (the plugin name from its @CapacitorPlugin / CAPBridgedPlugin receivers in the same file)."""
    cap_ns = defaultdict(set)
    for r in recs:
        if r.protocol == "capacitor":
            cap_ns[r.file.rel].add(r.namespace)
    n = defaultdict(int)
    helpers: dict = {}                   # Kotlin / Java helper forwarding its parameter as the event name: name -> (arg, via)
    found_by: dict = {}
    for nf in nfs:
        t = nf.t
        found = found_by.setdefault(nf.rel, [])      # (protocol, namespace, method, pos, via)
        props = _str_props(nf)
        if "notifyListeners" in t.code:
            ns = cap_ns.get(nf.rel)
            rx = OBJC_CAP_NOTIFY if nf.lang == "objc" else CAP_NOTIFY
            for m in rx.finditer(t.code):
                v, raw = _first_value(nf, t.code, m.end(), nf.lang != "objc")
                if not ns or len(ns) != 1:
                    continue
                vals = [v] if v is not None else _event_values(nf, m.start(), raw, props)
                if not vals:
                    dynamic.append({"file": nf.rel, "line": t.line(m.start()), "protocol": "capacitor-event",
                                    "what": f"notifyListeners({raw[:40]})"})
                    continue
                for v in vals:
                    found.append(("capacitor-event", next(iter(ns)), v, m.start(), "notifyListeners"))
        swift_named = nf.lang == "swift" and re.search(r"\bsendEvent\s*\(\s*(?:withName|name)\s*:", t.code)
        if RN_EMITTER_HINT.search(t.code) or swift_named:
            sites = []
            if nf.lang == "objc":
                sites = [(m, False, "sendEventWithName") for m in OBJC_SEND_EVENT.finditer(t.code)]
            else:
                hint = bool(RN_EMITTER_HINT.search(t.code))
                sites = [(m, True, "sendEvent") for m in RN_SEND_EVENT.finditer(t.code)
                         if hint or "withName" in m.group(0) or re.match(r"name\s*:", t.code[m.end():])]
                if re.search(r"RCTDeviceEventEmitter|RCTNativeAppEventEmitter", t.code):
                    sites += [(m, True, "RCTDeviceEventEmitter.emit") for m in RN_EMIT.finditer(t.code)]
                if nf.lang in ("kotlin", "java"):
                    sites += [(m, True, "emitDeviceEvent") for m in RN_EMIT_DEVICE.finditer(t.code)]
            for m, is_args, via in sites:
                # `fun sendEvent(name: String, ...)` / `private void sendEvent(...)`: the helper's own declaration
                if re.search(r"\b(?:fun|void|func)\s+$", t.code[max(0, m.start() - 12):m.start() + 1]) or \
                        re.search(r"\b(?:fun|void|func)\s+sendEvent\s*\($", t.code[max(0, m.start() - 20):m.end()]):
                    continue
                v, raw = _first_value(nf, t.code, m.end(), is_args)
                if v is None and (vals := _event_values(nf, m.start(), raw, props)):
                    for v in vals:
                        found.append(("react-native-event", v, None, m.start(), via))
                    continue
                if v is None:
                    h = _jvm_helper(nf, m.start(), raw) if nf.lang in ("kotlin", "java") else None
                    if h:                                           # `fun sendJSEvent(eventName: String, ..)`
                        helpers.setdefault(h[0], (h[1], via))
                        continue
                    if raw and not re.match(r"^[a-z]\w*$", raw):     # a parameter (`eventName`) is the helper itself
                        dynamic.append({"file": nf.rel, "line": t.line(m.start()), "protocol": "react-native-event",
                                        "what": f"{via}({raw[:40]})"})
                    continue
                found.append(("react-native-event", v, None, m.start(), via))
    extra = []
    if helpers and jvm_other:            # Kotlin / Java files without bridge code calling a helper
        hx = re.compile(r"(?<![\w$])(?:" + "|".join(map(re.escape, sorted(helpers))) + r")\s*\(")
        for rel, path in jvm_other:
            try:
                src = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if hx.search(src):
                extra.append(NativeFile(rel, src))
    for nf in list(nfs) + extra:         # calls of the helpers: `RNUtilsModuleImpl.sendJSEvent(Events.X.event, map)`
        if not helpers or nf.lang not in ("kotlin", "java"):
            continue
        t = nf.t
        for name, (idx, via) in helpers.items():
            if name not in t.code:
                continue
            for m in re.finditer(r"(?<![\w$])" + re.escape(name) + r"\s*\(", t.code):
                pre = t.code[t.code.rfind("\n", 0, m.start()) + 1:m.start()]
                if re.search(r"\bfun\s+(?:<[^>]*>\s*)?(?:[\w.]+\.)?$", pre) or (
                        re.match(r"^\s*(?:@\w+\s+)*(?:(?:public|private|protected|static|final|synchronized)\s+)*[\w.<>\[\]]+\s+$", pre)
                        and not re.search(r"\b(?:return|else|throw|new)\s+$", pre)):
                    continue                 # the declaration (`fun sendJSEvent(` / `void sendJSEvent(`)
                a = t.args(m.end() - 1)
                if len(a) <= idx:
                    continue
                raw = a[idx][1].strip()
                v = _str_value(nf, raw, _GLOBAL_CONSTS)
                if v is None:
                    if raw and not re.match(r"^[a-z]\w*$", raw) and not any(
                            d["file"] == nf.rel and d["line"] == t.line(m.start()) for d in dynamic):
                        dynamic.append({"file": nf.rel, "line": t.line(m.start()), "protocol": "react-native-event",
                                        "what": f"{name}({raw[:40]})"})
                    continue
                if any(f[3] == m.start() for f in found_by.get(nf.rel, ())):
                    continue                 # already a send site (`sendEvent(..)` itself)
                found_by.setdefault(nf.rel, []).append(("react-native-event", v, None, m.start(), f"{name} -> {via}"))
    for nf in list(nfs) + extra:
        t = nf.t
        for proto, ns, meth, pos, via in found_by.get(nf.rel, ()):
            line = t.line(pos)
            fn = None
            if nf.lang == "objc":            # inside RCT_EXPORT_METHOD(start:(NSString *)url) { ... }
                for em in re.finditer(r"RCT_(?:EXPORT|REMAP)_METHOD\s*\(\s*(?:\w+\s*,\s*)?(\w+)", t.code[:pos]):
                    ob = t.code.find("{", em.end())
                    if 0 <= ob < pos <= t.match(ob):
                        fn = em.group(1)
            hid = _handler(builder, by_file, Receiver(proto, ns, meth, nf, pos, fn=fn), nf, line)
            if hid is None:
                continue
            plat, _why = _platform_of(nf.rel, nf.lang)
            protocol_send(builder, proto, ns, meth, hid, nf.rel, line, "resolved", via=f"{via} (native)", platform=plat,
                          direction="to_app")
            n[proto] += 1
    return dict(n)


def _jvm_helper(nf: NativeFile, pos: int, raw: str):
    """The Kotlin / Java function enclosing `pos` when `raw` is one of its parameters: (name, parameter index)."""
    if not re.match(r"^[a-z]\w*$", raw or ""):
        return None
    t = nf.t
    best = None
    for m in JVM_FUN.finditer(t.code, 0, pos):
        name = m.group(1) or m.group(2)
        if name in ("if", "for", "while", "when", "switch", "catch", "synchronized", "return"):
            continue
        op = t.code.find("(", m.start())
        cl = t.match(op, "(", ")")
        ob = t.code.find("{", cl) if cl > 0 else -1
        if ob < 0 or not (ob < pos <= t.match(ob)):
            continue
        best = (name, t.src[op + 1:cl])
    if not best:
        return None
    for i, prm in enumerate(x for x in best[1].split(",")):
        if re.match(r"^\s*(?:@\w+\s+)*(?:final\s+)?" + re.escape(raw) + r"\s*:", prm) or \
                re.search(r"\bString\s+" + re.escape(raw) + r"\s*$", prm):
            return best[0], i
    return None


# ------------------------------------------------------------------ Cordova: native side (#61)
CORDOVA_JAVA_CLASS = re.compile(r"\bclass\s+(\w+)\s*(?:\([^)]*\))?\s*(?:extends|:)\s*(?:[\w.]+\.)?CordovaPlugin\b")
_CDV_V = r'("[^"]+"|[A-Z_][\w.]*)'
CORDOVA_ACTION = re.compile(r'\baction\s*\.\s*equals(?:IgnoreCase)?\s*\(\s*' + _CDV_V + r'\s*\)|' + _CDV_V +
                            r'\s*\.\s*equals(?:IgnoreCase)?\s*\(\s*action\s*\)|\baction\s*==\s*' + _CDV_V +
                            r'|^\s*case\s+' + _CDV_V + r'\s*:|^\s*' + _CDV_V + r'\s*->', re.M)
CORDOVA_OBJC_IFACE = re.compile(r"@interface\s+(\w+)\s*:\s*CDVPlugin\b")
CORDOVA_SWIFT_CLASS = re.compile(r"(?:@objc\s*\(\s*(\w+)\s*\)\s*)?(?:(?:public|open|final|internal)\s+)*class\s+(\w+)\s*:\s*CDVPlugin\b")
CORDOVA_OBJC_METHOD = re.compile(r"^\s*-\s*\(\s*void\s*\)\s*(\w+)\s*:\s*\(\s*CDVInvokedUrlCommand\s*\*\s*\)", re.M)
CORDOVA_SWIFT_METHOD = re.compile(r"(?:@objc\s*\(\s*(\w+):?\s*\)\s*)?(?:(?:public|open|internal|private|final|override|dynamic)\s+)*"
                                  r"func\s+(\w+)\s*\(\s*_?\s*\w+\s*:\s*CDVInvokedUrlCommand")
FEATURE_RE = re.compile(r"<feature\s+name\s*=\s*\"([^\"]+)\"\s*>(.*?)</feature>", re.S)
FEATURE_PARAM = re.compile(r"<param\s+name\s*=\s*\"(android-package|ios-package)\"\s+value\s*=\s*\"([^\"]+)\"")


def cordova_services(root: Path) -> dict:
    """plugin.xml / config.xml <feature name="Service"><param name="android-package" value="pkg.Cls"/> ->
    {class simple name: service}; skips node_modules and platform build copies."""
    out = {}
    for dp, dns, fns in os.walk(root):
        dns[:] = [d for d in dns if d not in ("node_modules", ".git", "build", "Pods") and not d.startswith(".")]
        for fn in fns:
            if fn not in ("plugin.xml", "config.xml"):
                continue
            try:
                txt = (Path(dp) / fn).read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for m in FEATURE_RE.finditer(txt):
                for pm in FEATURE_PARAM.finditer(m.group(2)):
                    out.setdefault(pm.group(2).rsplit(".", 1)[-1], m.group(1))
    return out


def scan_cordova(nf: NativeFile, out: list, services: dict) -> None:
    t = nf.t
    if nf.lang in ("java", "kotlin"):
        for m in CORDOVA_JAVA_CLASS.finditer(t.code):
            cls = m.group(1)
            c = nf.class_named(cls)
            lo, hi = (c[1], c[2]) if c else (m.start(), len(t.code))
            ex = re.compile(r"\bexecute\s*\(").search(t.code, lo, hi)
            if not ex:
                continue
            svc = services.get(cls, cls)
            seen = set()
            for am in CORDOVA_ACTION.finditer(t.src, ex.start(), hi):
                act = _str_value(nf, next(g for g in am.groups() if g), _GLOBAL_CONSTS)
                if not act or act in seen:
                    continue
                seen.add(act)
                out.append(Receiver("cordova", svc, act, nf, am.start(), fn="execute", cls=cls,
                                    conf="resolved" if cls in services else "heuristic", via="CordovaPlugin.execute"))
    elif nf.lang == "objc":
        for m in CORDOVA_OBJC_IFACE.finditer(t.code):
            cls = m.group(1)
            svc = services.get(cls, cls)
            for mm in CORDOVA_OBJC_METHOD.finditer(t.code):
                c = nf.class_at(mm.start())
                if c and c[0] != cls:
                    continue
                out.append(Receiver("cordova", svc, mm.group(1), nf, mm.start(1), fn=mm.group(1), cls=cls,
                                    conf="resolved" if cls in services else "heuristic", via="CDVPlugin method"))
        if "@implementation" in t.code and not CORDOVA_OBJC_IFACE.search(t.code):
            for im in OBJC_IMPL_RE.finditer(t.code):          # the @interface is in the .h file
                cls = im.group(1)
                if cls not in services:
                    continue
                for mm in CORDOVA_OBJC_METHOD.finditer(t.code, im.end()):
                    out.append(Receiver("cordova", services[cls], mm.group(1), nf, mm.start(1), fn=mm.group(1), cls=cls,
                                        conf="resolved", via="CDVPlugin method"))
    elif nf.lang == "swift":
        for m in CORDOVA_SWIFT_CLASS.finditer(t.code):
            objc_name, cls = m.group(1), m.group(2)
            svc = services.get(objc_name or cls, services.get(cls, objc_name or cls))
            end = t.match(t.code.find("{", m.end()))
            for mm in CORDOVA_SWIFT_METHOD.finditer(t.code, m.end(), end):
                out.append(Receiver("cordova", svc, mm.group(1) or mm.group(2), nf, mm.start(2), fn=mm.group(2), cls=cls,
                                    conf="resolved" if (objc_name or cls) in services else "heuristic",
                                    via="CDVPlugin method"))


# ------------------------------------------------------------------ Pigeon: native side
SWIFT_EXT = re.compile(r"\bextension\s+(\w+)\s*:\s*([^{]*)\{")
SUPER_NAME = re.compile(r"(?<![\w.])(?:\w+\.)*([A-Z]\w*)(\s*\()?")


def _decl_rx(lang: str, m: str):
    if lang == "kotlin":
        return re.compile(r"\bfun\s+(?:<[^>]*>\s*)?" + re.escape(m) + r"\s*\(")
    if lang == "swift":
        return re.compile(r"\bfunc\s+" + re.escape(m) + r"\s*[(<]")
    if lang == "java":
        return re.compile(r"[\w<>\[\],.?]+\s+" + re.escape(m) + r"\s*\([^;{)]*\)\s*(?:throws\s+[\w., ]+)?\{")
    return re.compile(r"^\s*-\s*\([^)]*\)\s*" + re.escape(m) + r"\b", re.M)


def _supers(nf: NativeFile, start: int) -> list[tuple[str, bool]]:
    """Supertype names of the class / extension declared at `start`: (name, called-as-constructor)."""
    code = nf.t.code
    head = code[start:code.find("{", start)]
    if nf.lang == "java":
        m = re.search(r"\b(?:extends|implements)\b(.*)", head, re.S)
        tail = m.group(1) if m else ""
    else:
        i, depth = -1, 0           # the ':' outside a Kotlin primary constructor `class X(ctx: Context) : Base(ctx), Api`
        for j, ch in enumerate(head):
            if ch in "(<":
                depth += 1
            elif ch in ")>":
                depth -= 1
            elif ch == ":" and depth == 0:
                i = j
                break
        tail = head[i + 1:] if i >= 0 else ""
    tail = re.sub(r"<[^<>]*>", "", tail)
    tail = re.sub(r"\bwhere\b.*", "", tail, flags=re.S)
    return [(m.group(1), bool(m.group(2))) for m in SUPER_NAME.finditer(tail)]


def scan_pigeon_hosts(nfs: list, apis: dict, out: list) -> None:
    """@HostApi implementations: a Kotlin / Swift / Java class (or Swift extension) that implements the generated
    interface / protocol `Api`; each API method is looked up in the class, then in its superclasses (an
    `ApiImplBase` holding the shared methods)."""
    host = {k: v for k, v in apis.items() if v["kind"] == "host"}
    if not host:
        return
    decls = defaultdict(list)            # class name -> [(nf, start, end)]
    for nf in nfs:
        for name, lo, hi in nf.classes:
            decls[name].append((nf, lo, hi))
        if nf.lang == "swift":
            for m in SWIFT_EXT.finditer(nf.t.code):
                decls[m.group(1)].append((nf, m.start(), nf.t.match(m.end() - 1)))

    def find(cls, meth, lang_fam, depth=0):
        for nf, lo, hi in decls.get(cls, ()):
            if FAM[nf.lang] != lang_fam:
                continue
            hit = _decl_rx(nf.lang, meth).search(nf.t.code, lo, hi)
            if hit:
                return nf, hit.start(), cls
        if depth < 3:
            for nf, lo, _hi in decls.get(cls, ()):
                for sup, _call in _supers(nf, lo):
                    if sup != cls and sup not in host:
                        got = find(sup, meth, lang_fam, depth + 1)
                        if got:
                            return got
        return None

    seen = set()
    for cls, items in decls.items():
        for nf, lo, _hi in items:
            for sup, called in _supers(nf, lo):
                if sup not in host or called:
                    continue
                for meth in host[sup]["methods"]:
                    got = find(cls, meth, FAM[nf.lang])
                    if not got:
                        continue
                    rnf, pos, rcls = got
                    key = (sup, meth, rnf.rel, pos)
                    if key in seen:
                        continue
                    seen.add(key)
                    out.append(Receiver("pigeon", sup, meth, rnf, pos, fn=meth, cls=rcls, conf="resolved",
                                        via=f"Pigeon HostApi ({cls} : {sup})"))


FAM = {"kotlin": "jvm", "java": "jvm", "swift": "apple", "objc": "apple"}


def pigeon_native_sends(builder, by_file, nfs: list, apis: dict) -> int:
    """@FlutterApi calls from native code: a variable / property holding the generated class
    (`flutterApi = BackgroundWorkerFlutterApi(binaryMessenger)`, `var api: XFlutterApi?`) and its method calls
    (`flutterApi?.onAndroidUpload(...)`) -> SENDS_TO endpoint:pigeon:<Api>#<method> from the enclosing native method."""
    flutter = {k: v for k, v in apis.items() if v["kind"] == "flutter"}
    n = 0
    for nf in nfs:
        code = nf.t.code
        for api, info in flutter.items():
            if api not in code:
                continue
            vars_ = set()
            for m in re.finditer(r"(\w+)\s*:\s*(?:[\w.]+\.)?" + re.escape(api) + r"\b\s*[?!]?", code):
                vars_.add(m.group(1))
            for m in re.finditer(r"(\w+)\s*=\s*(?:new\s+)?(?:[\w.]+\.)?" + re.escape(api) + r"\s*\(", code):
                vars_.add(m.group(1))
            meths = "|".join(map(re.escape, info["methods"]))
            if not meths:
                continue
            pats = []
            if vars_:
                pats.append(re.compile(r"(?<![\w])(?:(?:self|this)\s*\.\s*)?(?:" + "|".join(map(re.escape, sorted(vars_)))
                                       + r")\s*[?!]*\s*\.\s*(" + meths + r")\s*[({]"))
            pats.append(re.compile(re.escape(api) + r"\s*\([^()]*(?:\([^()]*\)[^()]*)*\)\s*[?!]*\s*\.\s*(" + meths + r")\s*[({]"))
            for rx in pats:
                for m in rx.finditer(code):
                    line = nf.t.line(m.start(1))
                    hid = _handler(builder, by_file, Receiver("pigeon", api, m.group(1), nf, m.start(1)), nf, line)
                    if hid is None:
                        continue
                    plat, _why = _platform_of(nf.rel, nf.lang)
                    protocol_send(builder, "pigeon", api, m.group(1), hid, nf.rel, line, "resolved",
                                  via="Pigeon FlutterApi call", platform=plat, direction="to_app")
                    n += 1
    return n


# ------------------------------------------------------------------ the index pass
def _platform_of(rel: str, lang: str) -> tuple[str, str]:
    parts = [p.lower() for p in rel.split("/")[:-1]]
    for p in ("android", "ios", "macos"):
        if p in parts:
            return p, f"{p}/ directory"
    for p in parts:
        if p in ("androidmain", "androidtest"):
            return "android", f"{p}/ source set"
        if p in ("iosmain",):
            return "ios", f"{p}/ source set"
    if lang in ("kotlin", "java"):
        return "android", f"{lang} native module"
    return "ios", f"{lang} native module"


def _native_files(project, scanned, builder) -> list[str]:
    out = []
    if scanned is not None:
        for e in (".kt", ".swift"):
            out += scanned.paths.get(e, [])
        out += getattr(scanned, "bridge_paths", [])
    return sorted(set(out))


TAURI_CMD = re.compile(r"#\[\s*(tauri::)?command\b[^\]]*\]\s*(?:#\[[^\]]*\]\s*)*(?:pub(?:\([^)]*\))?\s+)?"
                       r"(?:async\s+)?(?:unsafe\s+)?fn\s+([A-Za-z_]\w*)")
TAURI_HANDLER = re.compile(r"generate_handler!\s*\[((?:[^\[\]]|\[[^\]]*\])*)\]")     # `#![plugin(x)]` inside
TAURI_PLUGIN = re.compile(r"\bBuilder\s*(?:::\s*<[^>]*>\s*)?::\s*new\s*\(\s*\"([\w-]+)\"")


def tauri_receivers(project, builder) -> dict:
    """Tauri commands: `#[tauri::command] fn cmd` in the Rust core -> RECEIVED_BY from endpoint:tauri:<cmd> (a command
    registered by a plugin crate, `tauri::plugin::Builder::new("x")...generate_handler![cmd]`, is
    endpoint:tauri:plugin:x|cmd). attrs.registered: listed in a generate_handler! (an unregistered command cannot be
    invoked)."""
    root = Path(project.root)
    by_file = defaultdict(dict)
    for n in builder.nodes.values():
        if n.lang == "rust" and n.kind in ("function", "method") and n.file:
            by_file[n.file].setdefault(n.name.rsplit("::", 1)[-1], n.id)
    cmds, registered = [], {}
    for rel in sorted(by_file):
        try:
            src = (root / rel).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if "command" not in src and "generate_handler" not in src:
            continue
        src = strip_comments(src)
        bare_ok = bool(re.search(r"\buse\s+tauri::(?:\{[^}]*\bcommand\b|command\b)", src))
        for m in TAURI_CMD.finditer(src):
            if m.group(1) or bare_ok:
                cmds.append((rel, m.group(2), src.count("\n", 0, m.start(2)) + 1))
        plugin = TAURI_PLUGIN.search(src)
        for h in TAURI_HANDLER.finditer(src):
            for item in re.sub(r"#!?\[[^\]]*\]", " ", h.group(1)).split(","):
                name = item.strip().rsplit("::", 1)[-1].strip()
                if re.fullmatch(r"[A-Za-z_]\w*", name):
                    registered.setdefault(name, plugin.group(1) if plugin else None)
    n = 0
    for rel, name, line in cmds:
        nid = by_file[rel].get(name)
        if not nid:
            continue
        plugin = registered.get(name)
        ns = f"plugin:{plugin}|{name}" if plugin else name
        ep = protocol_receive(builder, "tauri", ns, None, nid, rel, line, "exact", via="#[tauri::command]",
                              process="core", registered=name in registered)
        if name not in registered:
            builder.nodes[ep].attrs["unregistered"] = True
        n += 1
    return {"commands": n, "registered": len(registered)} if n else {}


def apply(project, builder, scanned=None, sends_only: bool = False) -> dict:
    """Index pass: native receivers of every bridge protocol -> RECEIVED_BY edges (+ stub nodes for Java / ObjC) and
    platform marks on their files. Returns the bridges stats (empty when there is no bridge code)."""
    from .platforms import BIG, Cond, _plat_atom, mark
    tauri = tauri_receivers(project, builder)
    st0 = {"tauri": tauri} if tauri else {}
    st = _apply(project, builder, scanned, sends_only, mark, BIG, Cond, _plat_atom)
    if st0 or st:
        st = {**(st or {}), **st0}
    return st or {}


def _apply(project, builder, scanned, sends_only, mark, BIG, Cond, _plat_atom) -> dict:
    sends = [e for e in builder.edges.values() if e.kind == "SENDS_TO" or (e.kind == "TEST_CALLS" and e.attrs.get("orig") == "SENDS_TO")]
    root = Path(project.root)
    files = _native_files(project, scanned, builder)
    if not sends and not files:
        return {}
    clf = project.options.get("generated")
    apis = getattr(builder, "pigeon_apis", None) or {}
    # an API name or a name starting with it (`NativeSyncApiImplBase` holds methods its subclasses inherit)
    pigeon_rx = re.compile(r"\b(?:" + "|".join(map(re.escape, sorted(apis))) + r")") if apis else None
    nfs: list[NativeFile] = []
    other_consts: dict[str, str] = {}
    jvm_other: list = []
    _GLOBAL_CONSTS.clear()
    for rel in files:
        if clf is not None and clf.excludes(rel):
            continue
        try:
            src = (root / rel).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if len(src) > 2_000_000:
            continue
        for k, v in _enum_consts(rel, src).items():
            other_consts.setdefault(k, v)
        if not (TRIGGER.search(src) or (pigeon_rx and pigeon_rx.search(src))):
            if rel.endswith((".kt", ".java")):
                jvm_other.append((rel, root / rel))   # may call an event helper of a scanned file
            # not scanned, but its class constants name modules elsewhere (`getName() = FooImpl.NAME`): qualified only
            if "String" in src and (cm := CLASS_RE.search(src)):
                for m in CONST_RE.finditer(src):
                    other_consts.setdefault(f"{cm.group(1)}.{m.group(1)}", m.group(2))
            continue
        nf = NativeFile(rel, src)
        nfs.append(nf)
    for nf in nfs:            # NAME constants of other classes (getName() = FooImpl.NAME, channel names in a Constants file)
        cls = nf.classes[0][0] if nf.classes else None
        for k, v in nf.consts.items():
            _GLOBAL_CONSTS.setdefault(k, v)
            if cls:
                _GLOBAL_CONSTS.setdefault(f"{cls}.{k}", v)
    for k, v in other_consts.items():
        _GLOBAL_CONSTS.setdefault(k, v)
    if not nfs and not sends:
        return {}
    # TurboModule codegen spec classes: NativeFooSpec <- the JS spec file NativeFoo.ts naming the module
    spec_names = {}
    for e in sends:
        at = e.attrs.get("module_at")
        if at and e.attrs.get("via") and "TurboModuleRegistry" in e.attrs["via"]:
            stem = os.path.splitext(at.split(":")[0].rsplit("/", 1)[-1])[0]
            nid = e.dst
            spec_names.setdefault(stem + "Spec", nid.split(":", 2)[2].split("#")[0])
    objc_plugins, extern, handler_classes, pending = {}, {}, {}, []
    for nf in nfs:
        if nf.lang == "objc":
            scan_objc_capacitor(nf, objc_plugins)
    recs: list[Receiver] = []
    cdv = cordova_services(root) if any(re.search(r"CordovaPlugin|CDVPlugin", nf.t.code) for nf in nfs) else {}
    for nf in nfs:
        scan_cordova(nf, recs, cdv)
        scan_capacitor(nf, recs, objc_plugins)
        scan_react_native(nf, recs, spec_names, extern)
        scan_expo(nf, recs)
        scan_flutter(nf, recs, handler_classes, pending)
    # CAP_PLUGIN / RCT_EXTERN_MODULE registrations whose Swift class was not found: ObjC stubs at the macro
    swift_cls = {}
    for nf in nfs:
        if nf.lang == "swift":
            for m in SWIFT_CLASS.finditer(nf.t.code):
                end = nf.t.match(m.end() - 1)
                funcs = {f.group(1): f.start(1) for f in SWIFT_FUNC.finditer(nf.t.code, m.start(), end)}
                for nm in {m.group(1), m.group(2)} - {None}:
                    swift_cls.setdefault(nm, (nf, m.group(2), funcs))
    have = {(r.protocol, r.namespace, r.method, r.file.rel) for r in recs}
    for cls, (js, methods, nf, pos) in objc_plugins.items():
        sw = swift_cls.get(cls)
        for meth in methods:
            if sw and meth in sw[2]:
                if ("capacitor", js, meth, sw[0].rel) not in have:
                    recs.append(Receiver("capacitor", js, meth, sw[0], sw[2][meth], fn=meth, cls=sw[1], conf="exact",
                                         via="CAP_PLUGIN_METHOD"))
            else:
                recs.append(Receiver("capacitor", js, meth, nf, pos, fn=meth, cls=cls, conf="resolved", via="CAP_PLUGIN_METHOD"))
    for cls, items in extern.items():
        sw = swift_cls.get(cls)
        for ns, meth, nf, pos in items:
            node = None if sw and meth in sw[2] else builder.nodes.get(f"method:{cls}.{meth}")
            if node is not None and node.file:
                recs.append(Receiver("react-native", ns, meth, NativeFile(node.file, ""), 0, fn=meth, cls=cls, conf="exact",
                                     via="RCT_EXTERN_METHOD", lookup=(node.id, node.line)))
            elif sw and meth in sw[2]:
                recs.append(Receiver("react-native", ns, meth, sw[0], sw[2][meth], fn=meth, cls=sw[1], conf="exact",
                                     via="RCT_EXTERN_METHOD"))
            else:
                recs.append(Receiver("react-native", ns, meth, nf, pos, fn=meth, cls=cls, conf="resolved", via="RCT_EXTERN_METHOD"))
    # method sites / stream handlers in handler classes registered from another place
    fam = {"kotlin": "jvm", "java": "jvm", "swift": "apple", "objc": "apple"}
    for kind, meth, cls, nf, spos in pending:
        for hk, name, rnf, rpos in handler_classes.get(cls, ()):
            if kind == "method" and hk == "method" and fam[nf.lang] == fam[rnf.lang]:
                recs.append(Receiver("flutter", name, meth, nf, spos, conf="resolved", via="setMethodCallHandler(handler class)"))
    for cls, regs in handler_classes.items():
        for hk, name, rnf, rpos in regs:
            if hk != "event":
                continue
            target = next(((nf, c) for nf in nfs for c in nf.classes if c[0] == cls and fam[nf.lang] == fam[rnf.lang]), None)
            if target:
                nf, c = target
                lm = re.compile(r"\bonListen\b").search(nf.t.code, c[1], c[2])
                recs.append(Receiver("flutter-event", name, None, nf, lm.start() if lm else c[1], conf="resolved",
                                     via="setStreamHandler(handler class)"))
            else:
                recs.append(Receiver("flutter-event", name, None, rnf, rpos, conf="heuristic", via="setStreamHandler"))
    if apis:
        scan_pigeon_hosts(nfs, apis, recs)
    # receivers -> handler nodes
    by_file = defaultdict(list)
    for n in builder.nodes.values():
        if n.file and n.kind in ("method", "function") and n.line:
            by_file[n.file].append(n)
    pigeon_sends = pigeon_native_sends(builder, by_file, nfs, apis) if apis else 0
    flutter_sends = flutter_native_sends(builder, by_file, nfs)
    dynamic = list(getattr(builder, "bridge_dynamic", None) or [])
    event_sends = event_native_sends(builder, by_file, nfs, recs, dynamic, jvm_other)
    n_recv, n_stub, marked = 0, 0, set()
    stats = defaultdict(lambda: defaultdict(int))
    seen = set()
    for r in recs:
        nf = r.file
        if r.lookup:                         # resolved to a language plugin node directly
            hid, line = r.lookup
        else:
            line = nf.t.line(r.pos)
            hid = _handler(builder, by_file, r, nf, line)
        if hid is None:
            continue
        if builder.nodes[hid].attrs.get("bridge_stub"):
            n_stub += 1
        plat, why = _platform_of(nf.rel, nf.lang)
        key = (r.protocol, r.namespace, r.method, hid)
        if key in seen:
            continue
        seen.add(key)
        protocol_receive(builder, r.protocol, r.namespace, r.method, hid, nf.rel, line, r.conf, platform=plat, via=r.via,
                         api=r.api)
        if r.api:
            builder.nodes["endpoint:" + endpoint_key(r.protocol, r.namespace, r.method)].attrs["api"] = r.api
        stats[r.protocol]["receivers"] += 1
        n_recv += 1
        if nf.rel not in marked:
            marked.add(nf.rel)
            mark(builder, nf.rel, 1, BIG, Cond("tree", _plat_atom(plat), why), line=1)
            builder.platform_marks[-1]["bridge"] = True
    for e in sends:
        stats[e.attrs.get("protocol") or e.dst.split(":", 2)[1]]["sends" if e.kind == "SENDS_TO" else "test_sends"] += 1
    if pigeon_sends:
        stats["pigeon"]["native_sends"] += pigeon_sends
    if flutter_sends:
        stats["flutter"]["native_sends"] += flutter_sends
    for proto, k in event_sends.items():
        stats[proto]["native_sends"] += k
    out = {"native_files_scanned": len(nfs), "receivers": n_recv, "stub_nodes": n_stub,
           "per_protocol": {k: dict(v) for k, v in sorted(stats.items())}}
    if dynamic:
        out["dynamic"] = {"count": len(dynamic), "samples": dynamic[:20]}
    return out


_DECL_RX = {
    "java": re.compile(r"(?:(?:public|private|protected|static|final|synchronized|native|abstract)\s+)+[\w<>\[\],.?]+\s+(\w+)\s*"
                       r"\([^;{)]*\)\s*(?:throws\s+[\w., ]+)?\{"),
    "objc": re.compile(r"^\s*[-+]\s*\([^)]*\)\s*(\w+)[^{;]*\{", re.M),
    "swift": re.compile(r"\bfunc\s+(\w+)\s*(?:<[^>]*>)?\s*\([^{]*\{"),
    "kotlin": re.compile(r"\bfun\s+(?:<[^>]*>\s*)?(?:[\w.]+\.)?(\w+)\s*\([^{=]*\{"),
}


def _enclosing_decl(nf: NativeFile, pos: int) -> tuple[str, int, int] | None:
    """Enclosing Java / ObjC method (name, start, end) for receivers in files without a language plugin."""
    t = nf.t
    rx = _DECL_RX[nf.lang]
    best = None
    for m in rx.finditer(t.code, 0, pos + 1):
        end = t.match(m.end() - 1)
        if m.start() <= pos <= end or m.start(1) <= pos <= m.end(1):
            best = (m.group(1), m.start(), end)
    return best


def _handler(builder, by_file, r: Receiver, nf: NativeFile, line: int) -> str | None:
    cands = by_file.get(nf.rel, [])
    if r.fn:
        hit = [n for n in cands if n.name == r.fn and n.line <= line <= (n.end_line or n.line)]
        hit = hit or [n for n in cands if n.name == r.fn and abs(n.line - line) <= 3]
        if hit:
            return min(hit, key=lambda n: (n.end_line or n.line) - n.line).id
    else:
        hit = [n for n in cands if n.line <= line <= (n.end_line or n.line)]
        if hit:
            return min(hit, key=lambda n: (n.end_line or n.line) - n.line).id
    # no language plugin node (Java, ObjC, or the Kotlin / Swift plugin did not run): a stub method node
    fn, lo, hi = r.fn, r.pos, r.pos
    d = _enclosing_decl(nf, r.pos)
    if d and (not r.fn or d[0] == r.fn or nf.lang == "java"):
        fn, lo, hi = r.fn or d[0], d[1], d[2]
    if not fn:
        return None
    c = nf.class_at(r.pos)
    cls = r.cls or (c[0] if c else None)
    if nf.lang == "objc":
        fqn = f"objc:{cls}.{fn}" if cls else f"objc:{nf.rel}#{fn}"
    elif nf.lang == "swift":
        fqn = f"{cls}.{fn}" if cls else f"{nf.rel}#{fn}"
    else:
        fqn = ".".join(x for x in (nf.package, cls, fn) if x)
    nid = builder.add_node("method", fqn, fn, fqn=fqn, file=nf.rel, line=nf.t.line(lo), end_line=nf.t.line(hi), lang=nf.lang,
                           attrs={"bridge_stub": True})
    by_file[nf.rel].append(builder.nodes[nid])
    return nid


def project_targets(project, builder) -> list[str]:
    """Targets of a project without platform-specific code (platforms.apply returned nothing)."""
    from .platforms import declared_targets
    langs = {n.lang for n in builder.nodes.values() if n.lang}
    try:
        return declared_targets(Path(project.root), project.options.get("config") or {}, [], langs)[0]
    except Exception:  # noqa: BLE001
        return []


def _process_roles(builder, eps, recv, send) -> None:
    """Electron / Tauri: process role of each file taking part in IPC (attrs.process on its module node): main,
    preload (exposes a context bridge), renderer (Electron), webview / core (Tauri)."""
    roles = defaultdict(set)
    for nid, n in eps.items():
        if n.attrs.get("protocol") not in PROCESS_PROTOCOLS:
            continue
        for e in recv[nid]:
            dn = builder.nodes.get(e.dst)
            if dn and dn.file and e.attrs.get("process"):
                roles[dn.file].add(e.attrs["process"])
        for e in send[nid]:
            sn = builder.nodes.get(e.src)
            if sn and sn.file and e.attrs.get("process"):
                roles[sn.file].add(e.attrs["process"])
    if not roles:
        return
    for n in builder.nodes.values():
        if n.kind in ("module", "file", "mod", "crate") and n.file in roles:
            r = roles[n.file]
            n.attrs["process"] = "preload" if "preload" in r else next(iter(r)) if len(r) == 1 else "+".join(sorted(r))


def finalize(project, builder, st: dict, targets: list[str] | None) -> dict:
    """Per endpoint: platforms receiving it, sides seen, checks. Called after platforms.apply (targets known)."""
    eps = {nid: n for nid, n in builder.nodes.items() if n.kind == "endpoint" and n.attrs.get("protocol") in PROTOCOLS}
    if not eps:
        return st
    recv = defaultdict(list)
    send = defaultdict(list)
    tsend = defaultdict(list)
    for e in builder.edges.values():
        if e.kind == "RECEIVED_BY" and e.src in eps:
            recv[e.src].append(e)
        elif e.kind == "SENDS_TO" and e.dst in eps:
            send[e.dst].append(e)
        elif e.kind == "TEST_CALLS" and e.attrs.get("orig") == "SENDS_TO" and e.dst in eps:
            tsend[e.dst].append(e)
    ns_plat = defaultdict(set)          # (protocol, namespace) -> platforms implementing any method of it
    for nid, es in recv.items():
        a = eps[nid].attrs
        for e in es:
            ns_plat[(a["protocol"], a["namespace"])].add(e.attrs.get("platform"))
    ev_sent = defaultdict(set)            # event protocol -> namespaces some native code emits on (None: any)
    for nid, es in send.items():
        a = eps[nid].attrs
        if a["protocol"] in EVENT_PROTOCOLS and es:
            ev_sent[a["protocol"]].update((None, a["namespace"]))
    proto_recv = defaultdict(set)
    for nid in recv:
        proto_recv[eps[nid].attrs["protocol"]].add(None)
    _process_roles(builder, eps, recv, send)
    # macOS counts only where some bridge module is implemented for it (a Flutter macos/ runner)
    used = {e.attrs.get("platform") for es in recv.values() for e in es}
    mobile = [p for p in (targets or []) if p in ("android", "ios") or (p == "macos" and p in used)]
    checks = defaultdict(int)
    root = Path(project.root)
    pj_cache: dict = {}
    for nid, n in eps.items():
        a = n.attrs
        rp = sorted({e.attrs.get("platform") for e in recv[nid]} - {None})
        a["platforms_received"] = rp
        a["side"] = "both" if send[nid] and recv[nid] else "send" if send[nid] or tsend[nid] else "receive"
        # native -> app (Pigeon @FlutterApi, a native invokeMethod handled in Dart): the app side receives on every
        # platform; the platforms are the senders'
        to_app = any(e.attrs.get("direction") == "to_app" for e in send[nid]) or (
            recv[nid] and all((builder.nodes.get(e.dst) is not None and builder.nodes[e.dst].lang in APP_LANGS)
                              for e in recv[nid]))
        if to_app:
            a["direction"] = "to_app"
            a["platforms_sending"] = sorted({e.attrs.get("platform") for e in send[nid]} - {None})
        elif a["protocol"] in ("pigeon", "flutter", "flutter-event", "capacitor", "react-native", "cordova"):
            a["direction"] = "to_native"
        a.pop("checks", None)
        ck = []
        proc = a["protocol"] in PROCESS_PROTOCOLS
        impl = ns_plat.get((a["protocol"], a["namespace"]))
        if proc:
            # one app, two processes: a receiver-less channel is a miss when the other side is in the repo at all
            ext_pkg = any(e.attrs.get("external") for e in send[nid] + tsend[nid])
            impl = None if ext_pkg else (proto_recv.get(a["protocol"]) or None)
        base = a.get("method") in BASE_METHODS.get(a["protocol"], ())
        if base:
            a["base_method"] = True        # the framework's base class implements it on every platform; a module may override
        if a["protocol"] in EVENT_PROTOCOLS and (send[nid] or tsend[nid]) and not recv[nid]:
            a["no_listener"] = True        # emitted natively, no JS listener found (one may sit in a library / dynamic name)
        elif (send[nid] or tsend[nid]) and not recv[nid]:
            if impl:
                if not base:
                    ck.append("no_receiver")
            else:
                a["external"] = True
                ext = sorted({e.attrs.get("external") for e in send[nid] + tsend[nid]} - {None})
                if ext:
                    a["package"] = ext[0]
        if a["protocol"] in EVENT_PROTOCOLS and recv[nid] and not send[nid]:
            # a listener for an event no native code in the repo emits: a miss only where the repo's native side
            # emits events at all (Capacitor: the same plugin's native code is here); else a library's event
            if a["protocol"] == "capacitor-event":
                here = a["namespace"] in ev_sent.get(a["protocol"], ()) or ns_plat.get(("capacitor", a["namespace"]))
            else:
                # NativeEventEmitter(Module) of a module implemented here; DeviceEventEmitter: where the repo's native
                # code emits events at all
                mods = {e.attrs.get("emitter_module") for e in recv[nid]}
                here = any(m and ns_plat.get(("react-native", m)) for m in mods)
                if not here and not any(mods):
                    # DeviceEventEmitter / an emitter of an unknown module: often an in-app JS event bus whose emits
                    # pass the name in a variable; not a bridge miss, not a library either
                    base = True
                    here = True
            if not here:
                a["external"] = True
                ext = sorted({e.attrs.get("external") for e in recv[nid]} - {None})
                if ext:
                    a["package"] = ext[0]
                base = True
        if recv[nid] and not send[nid] and not base:
            ck.append("no_sender" if not tsend[nid] else "test_sender_only")
        if a.get("unregistered"):
            ck.append("unregistered")
        if recv[nid] and impl and not base and not proc and not to_app:
            expected = mobile or sorted(impl - {None})
            if a["protocol"] == "react-native":
                declared = set()
                for e in recv[nid]:
                    dn = builder.nodes.get(e.dst)
                    cp = dn and dn.file and _codegen_platforms(root, dn.file, pj_cache)
                    if not cp:
                        declared = None
                        break
                    declared |= set(cp)
                if declared:
                    expected = [p for p in expected if p in declared]
            # Flutter: only the platform folders of the sending app's package (an iOS-only sample has no android/)
            if a["protocol"] in ("flutter", "flutter-event", "pigeon") and send[nid]:
                folders = [_flutter_folders(root, e.file, pj_cache) for e in send[nid]]
                if all(f is not None for f in folders):
                    have = set().union(*folders)
                    expected = [p for p in expected if p in have]
            # every sender is gated to some platforms (`Platform.OS === 'android'`, kIsWeb, ...): only those need it
            gated = [e.attrs.get("platforms") for e in send[nid]]
            if gated and all(g is not None for g in gated):     # [] : on none of the targets (#74)
                only = sorted({p for g in gated for p in g})
                a["sender_platforms"] = only
                expected = [p for p in expected if p in only]
            miss = [p for p in expected if p not in rp]
            if miss:
                a["missing_on"] = miss
                ck.append("missing_on")
            else:
                a.pop("missing_on", None)
        if ck:
            a["checks"] = ck
            for c in ck:
                checks[c] += 1
        if a.get("external"):
            checks["external"] += 1
    st = dict(st or {})
    st["endpoints"] = len(eps)
    st["linked"] = sum(1 for nid in eps if send[nid] and recv[nid])
    st["checks"] = dict(sorted(checks.items()))
    st["targets"] = mobile
    return st


# ------------------------------------------------------------------ query: cg bridges / MCP bridges
def _attrs(r) -> dict:
    return json.loads(r["attrs"] or "{}") if r["attrs"] else {}


def bridges(st, pattern: str | None = None, protocol: str | None = None, unmatched: bool = False) -> dict:
    """Bridge endpoints with their senders (JS / Dart call sites, entry points reaching them) and receivers per
    platform, plus the checks (missing_on, no_receiver, no_sender, external)."""
    rows = [dict(r) for r in st.q("SELECT * FROM nodes WHERE kind='endpoint' ORDER BY id")]
    out = []
    for n in rows:
        a = _attrs(n)
        if a.get("protocol") not in PROTOCOLS or (protocol and a["protocol"] != protocol):
            continue
        name = n["name"]
        if pattern:
            p = pattern[len("endpoint:"):] if pattern.startswith("endpoint:") else pattern
            if not (fnmatch.fnmatchcase(name, p) or fnmatch.fnmatchcase(n["id"].split(":", 1)[1], p)
                    or p.lower() in name.lower()):
                continue
        if unmatched and not (a.get("checks") or a.get("external")):
            continue
        item = {"id": n["id"], "protocol": a["protocol"], "namespace": a.get("namespace"), "method": a.get("method"),
                "api": a.get("api"), "platforms_received": a.get("platforms_received") or [],
                "checks": a.get("checks") or [], "missing_on": a.get("missing_on") or [], "external": bool(a.get("external")),
                "package": a.get("package"), "test_only": bool(a.get("test_only")),
                "base_method": bool(a.get("base_method")), "sender_platforms": a.get("sender_platforms"),
                "direction": a.get("direction"), "platforms_sending": a.get("platforms_sending") or [],
                "no_listener": bool(a.get("no_listener")), "senders": [], "test_senders": [],
                "receivers": []}
        for r in st.q("SELECT src, kind, file, line, confidence, attrs FROM edges WHERE dst=? AND kind IN ('SENDS_TO','TEST_CALLS') "
                      "ORDER BY file, line", (n["id"],)):
            ea = _attrs(r)
            ents = {e["entry_kind"]: e["entry_count"] for e in
                    st.q("SELECT entry_kind, entry_count FROM node_entry WHERE node_id=?", (r["src"],))}
            s = {"fn": r["src"], "at": f"{r['file']}:{r['line']}", "confidence": r["confidence"], "via": ea.get("via"),
                 "entry_kinds": ents}
            if ea.get("process"):
                s["process"] = ea["process"]
            (item["senders"] if r["kind"] == "SENDS_TO" else item["test_senders"]).append(s)
        for r in st.q("SELECT dst, file, line, confidence, attrs FROM edges WHERE src=? AND kind='RECEIVED_BY' ORDER BY file, line",
                      (n["id"],)):
            ea = _attrs(r)
            item["receivers"].append({"handler": r["dst"], "at": f"{r['file']}:{r['line']}", "platform": ea.get("platform"),
                                      "via": ea.get("via"), "confidence": r["confidence"],
                                      "stub": bool(_attrs(st.node(r["dst"]) or {"attrs": None}).get("bridge_stub")),
                                      **({"process": ea["process"]} if ea.get("process") else {})})
        out.append(item)
    summ = defaultdict(lambda: defaultdict(int))
    for i in out:
        s = summ[i["protocol"]]
        s["endpoints"] += 1
        s["linked"] += bool(i["senders"] and i["receivers"])
        for c in i["checks"]:
            s[c] += 1
        s["external"] += i["external"]
    stats = (st.meta().get("stats") or {}).get("bridges") or {}
    # bridge calls cg could not name (NativeModules[x], addListener(evtVar), cordova.exec(.., svc, act)): listed, not linked
    dyn = [d for d in ((stats.get("dynamic") or {}).get("samples") or [])
           if (not protocol or d.get("protocol") == protocol or (d.get("protocol") or "").startswith(protocol + "-"))
           and (not pattern or pattern.lower() in (d.get("what") or "").lower() or pattern.lower() in (d.get("file") or "").lower())]
    return {"pattern": pattern, "protocol": protocol, "endpoints": out,
            "summary": {k: dict(v) for k, v in sorted(summ.items())},
            "unresolved": dyn, "unresolved_total": (stats.get("dynamic") or {}).get("count", 0) if not (protocol or pattern) else len(dyn),
            "stats": stats}


def render_bridges(res: dict, max_items: int = 60) -> str:
    from .query import short_id
    eps = res["endpoints"]
    dyn = res.get("unresolved") or []
    if not eps and not dyn:
        return "no bridge endpoint matches " + repr(res["pattern"]) if res["pattern"] else "no web / native bridge calls in this graph"
    L = []
    for p, s in res["summary"].items():
        extra = ", ".join(f"{k} {v}" for k, v in s.items() if k not in ("endpoints", "linked") and v)
        L.append(f"{p}: {s['endpoints']} endpoint(s), {s.get('linked', 0)} linked" + (f"  ({extra})" if extra else ""))
    tg = (res.get("stats") or {}).get("targets")
    if tg:
        L.append(f"mobile targets: {', '.join(tg)}")
    detail = len(eps) <= 6
    for i in eps[:max_items]:
        flags = []
        proc = i["protocol"] in PROCESS_PROTOCOLS
        if i["missing_on"]:
            flags.append("MISSING ON " + ", ".join(i["missing_on"]))
        for c in i["checks"]:
            if c == "no_receiver":
                flags.append("NO RECEIVER (this app handles other channels / commands, not this one)" if proc else
                             "NO NATIVE RECEIVER (the module is implemented here, this method is not)")
            elif c == "no_sender":
                flags.append("no sender" if proc else "no native sender" if i.get("direction") == "to_app" and
                             i["protocol"] in EVENT_PROTOCOLS else "no JS / Dart sender")
            elif c == "unregistered":
                flags.append("NOT REGISTERED (missing from generate_handler!)")
            elif c == "test_sender_only":
                flags.append("sent from tests only")
        if i.get("no_listener"):
            flags.append("no JS listener found")
        if i["external"]:
            flags.append("external" + (f" ({i['package']})" if i.get("package") else ": implemented outside this repo"))
        where = (f"received in: {', '.join(sorted({r.get('process') or '?' for r in i['receivers']})) or '-'}" if proc else
                 f"sent from: {', '.join(i['platforms_sending']) or '-'}" if i["protocol"] in EVENT_PROTOCOLS else
                 f"received on: {', '.join(i['platforms_received']) or '-'}")
        L.append(f"{i['id'].split(':', 1)[1]}  {where}"
                 + (f"  [{i['api']}]" if i.get("api") else "") + (f"  ! {'; '.join(flags)}" if flags else ""))
        if detail:
            for s in i["senders"][:8]:
                ek = ", ".join(f"{k}({v})" for k, v in sorted(s["entry_kinds"].items()))
                L.append(f"    sent by {short_id(s['fn'])} @ {s['at']} [{s['confidence']}"
                         + (f", {s['process']}" if s.get("process") else "") + "]" + (f"  entries: {ek}" if ek else ""))
            for s in i["test_senders"][:4]:
                L.append(f"    test {short_id(s['fn'])} @ {s['at']}")
            for r in i["receivers"]:
                L.append(f"    received by {short_id(r['handler'])} @ {r['at']} [{r.get('process') or r['platform']}, {r['via']}]"
                         + ("  (stub: no language plugin)" if r["stub"] else ""))
        else:
            L.append(f"    senders {len(i['senders'])}" + (f" (+{len(i['test_senders'])} test)" if i["test_senders"] else "")
                     + f", receivers {len(i['receivers'])}")
    if len(eps) > max_items:
        L.append(f"... {len(eps) - max_items} more")
    if dyn:
        L.append(f"unresolved (name not a literal cg can evaluate): {res.get('unresolved_total') or len(dyn)}")
        for d in dyn[:12]:
            L.append(f"    {d['protocol']}: {d['what']} @ {d['file']}:{d['line']}")
        if len(dyn) > 12:
            L.append(f"    ... {len(dyn) - 12} more")
    return "\n".join(L)
