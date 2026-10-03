"""Bridge follow-ups (#54): Pigeon @HostApi / @FlutterApi (tests/bridge_fixtures/flutter_pigeon_app), native ->
Dart MethodChannel calls (`invokeMethod` in Kotlin / Swift received by a Dart `setMethodCallHandler`), and
TypeScript monorepos without a root tsconfig.json (each package's tsconfig joins one program)."""
import json
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from codegraph import bridges as B  # noqa: E402
from codegraph.core.store import GraphStore  # noqa: E402
from codegraph.indexer import index_project  # noqa: E402
from codegraph.plugins.dart.plugin import find_dart  # noqa: E402
from codegraph.plugins.ts import plugin as TS  # noqa: E402

FX = ROOT / "tests" / "bridge_fixtures" / "flutter_pigeon_app"
TS_DEPS = ROOT / "codegraph" / "plugins" / "ts" / "extractor" / "node_modules"
needs_ts = pytest.mark.skipif(not TS_DEPS.exists(), reason="run `npm ci` in codegraph/plugins/ts/extractor")
needs_dart = pytest.mark.skipif(find_dart() is None, reason="Dart SDK not found (set $DART or put dart on PATH)")
_G: dict = {}


def graph():
    if "g" not in _G:
        db = Path(tempfile.mkdtemp(prefix="codegraph-pigeon-")) / "g.db"
        res = index_project(FX, db, "pigeon")
        _G["g"] = (GraphStore(str(db)), res)
    return _G["g"]


def eps(st) -> dict:
    out = {}
    for e in B.bridges(st)["endpoints"]:
        a = st.node(e["id"])["attrs"]
        e["attrs"] = json.loads(a) if isinstance(a, str) else (a or {})
        out[e["id"].split(":", 1)[1]] = e
    return out


def recv(e) -> set:
    return {(r["platform"], r["handler"]) for r in e["receivers"]}


def senders(e) -> set:
    return {s["fn"] for s in e["senders"]}


@needs_dart
def test_pigeon_host_api():
    st, res = graph()
    ep = eps(st)
    h = ep["pigeon:SyncApi#hashAll"]
    # the Dart call on a field typed with the HostApi; Kotlin implements it through an `ImplBase` superclass (a
    # primary constructor with a default value), Swift in the class conforming to the protocol
    assert senders(h) == {"method:lib/sync.dart#SyncService.run"}
    assert recv(h) == {("android", "method:com.example.pig.SyncApiImplBase.hashAll"), ("ios", "method:SyncApiImpl.hashAll")}
    assert h["checks"] == [] and h["attrs"]["direction"] == "to_native"
    # `ref.read(syncApiProvider)` with `final syncApiProvider = Provider<SyncApi>(...)`
    c = ep["pigeon:SyncApi#clearCheckpoint"]
    assert senders(c) == {"function:lib/sync.dart#reset"}
    assert recv(c) == {("android", "method:com.example.pig.SyncApiImpl.clearCheckpoint"), ("ios", "method:SyncApiImpl.clearCheckpoint")}
    p = res["plugins"]["dart"]["pigeon"]
    assert p["host_apis"] == 1 and p["flutter_apis"] == 1 and p["sends"] == 2


@needs_dart
def test_pigeon_flutter_api_native_to_dart():
    st, _ = graph()
    e = eps(st)["pigeon:UploadEventsApi#onUpload"]
    assert senders(e) == {"method:com.example.pig.MainActivity.uploaded"}
    assert recv(e) == {(None, "method:lib/sync.dart#UploadEventsHandler.onUpload")}
    assert e["attrs"]["direction"] == "to_app" and e["attrs"]["platforms_sending"] == ["android"]
    assert e["checks"] == [] and e["missing_on"] == []


@needs_dart
def test_native_invoke_method_to_dart_handler():
    st, res = graph()
    ep = eps(st)
    e = ep["flutter:example.dev/counter#reportCounter"]
    # Kotlin `channel.invokeMethod(...)` on a lateinit property assigned in configureFlutterEngine; Swift on an
    # optional property; Dart `setMethodCallHandler(_handleMessage)` with `call.method == 'reportCounter'`
    assert senders(e) == {"method:com.example.pig.MainActivity.report", "method:AppDelegate.report"}
    assert recv(e) == {(None, "method:lib/counter.dart#CounterModel._handleMessage")}
    assert e["attrs"]["direction"] == "to_app" and sorted(e["attrs"]["platforms_sending"]) == ["android", "ios"]
    assert e["checks"] == []
    # a closure handler with `switch (call.method)`: received, nothing native sends it
    for m in ("setCellNumber", "reset"):
        c = ep[f"flutter:example.dev/cell#{m}"]
        assert recv(c) == {(None, "method:lib/counter.dart#Cell.listen")} and c["checks"] == ["no_sender"]
    assert res["plugins"]["dart"]["platform_channel_sends"]["dart_handlers"] == 3
    assert res["bridges"]["per_protocol"]["flutter"]["native_sends"] == 2


def test_class_re_primary_constructor_default():
    nf = B.NativeFile("a/X.kt", 'class X(ctx: Context, private val tag: String = "x") : Base(ctx), Api {\n fun f() {}\n}\n')
    assert [c[0] for c in nf.classes] == ["X"]
    assert B._supers(nf, nf.classes[0][1]) == [("Base", True), ("Api", False)]


def _pkg(root: Path, name: str, files: dict, deps: dict | None = None):
    d = root / name
    (d / "src").mkdir(parents=True)
    (d / "package.json").write_text(json.dumps({"name": name, "dependencies": deps or {}}))
    (d / "tsconfig.json").write_text(json.dumps({"compilerOptions": {"target": "es2020", "module": "commonjs",
                                                                     "rootDir": "src", "outDir": "dist", "strict": True},
                                                 "include": ["src"]}))
    for k, v in files.items():
        (d / "src" / k).write_text(v)


def test_package_tsconfigs(tmp_path):
    (tmp_path / "package.json").write_text('{"name": "mono", "private": true}')
    _pkg(tmp_path, "core", {"index.ts": "export function add(a: number, b: number): number { return a + b; }\n"})
    (tmp_path / "packages").mkdir()
    _pkg(tmp_path / "packages", "cli", {"main.ts": "import { add } from '../../../core/src/index';\n"
                                                   "export function run(): number { return add(1, 2); }\n"})
    (tmp_path / "node_modules" / "dep").mkdir(parents=True)
    (tmp_path / "node_modules" / "dep" / "package.json").write_text("{}")
    (tmp_path / "node_modules" / "dep" / "tsconfig.json").write_text("{}")
    got = TS.package_tsconfigs(tmp_path)
    assert sorted(got) == ["core/tsconfig.json", "packages/cli/tsconfig.json"]
    (tmp_path / "tsconfig.json").write_text("{}")
    assert TS.package_tsconfigs(tmp_path) == []


@needs_ts
def test_ts_monorepo_without_root_tsconfig(tmp_path):
    (tmp_path / "package.json").write_text('{"name": "mono", "private": true}')
    _pkg(tmp_path, "core", {"index.ts": "export function add(a: number, b: number): number { return a + b; }\n"})
    _pkg(tmp_path, "cli", {"main.ts": "import { add } from '../../core/src/index';\n"
                                      "export function run(): number { return add(1, 2); }\n"})
    db = tmp_path / "g.db"
    res = index_project(tmp_path, db, "mono")
    st = GraphStore(str(db))
    ts = res["plugins"]["typescript"]
    assert ts["config"]["package_tsconfigs"] == 2
    rows = st.q("SELECT src, dst FROM edges WHERE kind='CALLS'")
    assert any(r["src"].endswith("cli/src/main.ts#run") and r["dst"].endswith("core/src/index.ts#add") for r in rows)
