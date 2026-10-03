"""Platform-specific code (codegraph/platforms.py): platform tags, --platform filtered queries and divergence findings
on one small fixture per idiom (tests/platform_fixtures): Rust #[cfg] / cfg!, C #ifdef, Dart conditional imports and
Platform.isX / kIsWeb, React Native .ios.ts / .native.ts files, Platform.OS and Platform.select."""
import json
import subprocess
import sys

import pytest

from native_util import ROOT, TS_SKIP, have_tree_sitter, index

sys.path.insert(0, str(ROOT))
from codegraph import platforms as PF  # noqa: E402
from codegraph import query as Q  # noqa: E402
from codegraph.config import ConfigError, parse  # noqa: E402
from codegraph.core.store import GraphStore  # noqa: E402
from codegraph.plugins.dart.plugin import find_dart  # noqa: E402

FX = ROOT / "tests" / "platform_fixtures"
_C = {}
needs_ts = pytest.mark.skipif(not have_tree_sitter(), reason=TS_SKIP)
needs_dart = pytest.mark.skipif(find_dart() is None, reason="Dart SDK not found (set $DART or put dart on PATH)")


def graph(name: str, **envs) -> tuple[GraphStore, dict]:
    key = (name, tuple(sorted(envs.items())))
    if key not in _C:
        db, res = index(FX / name, **envs)
        _C[key] = (GraphStore(str(db)), res, db)
    st, res, _ = _C[key]
    return st, res


def tags(st: GraphStore, nid: str) -> list[str] | None:
    r = st.node(nid)
    assert r, nid
    return json.loads(r["attrs"] or "{}").get("platforms")


def callers(st, spec, platform=None):
    return {c["id"] for c in Q.impact(st, spec, platform=platform)["callers"]}


def cli(*args) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-m", "codegraph.cli", *args], cwd=ROOT, capture_output=True, text=True)


# ------------------------------------------------------------------ evaluation
def test_condition_evaluation():
    from codegraph.plugins.native.gates import parse_cfg
    t = parse_cfg('all(unix, not(target_os = "macos"))')
    assert [p for p in PF.KNOWN if PF.eval_tree(t, p)] == ["linux", "ios", "android"]
    assert PF.eval_tree(parse_cfg('feature = "x"'), "linux") is None                 # not a platform: unknown
    assert PF.eval_tree(("any", [parse_cfg("windows"), parse_cfg('feature = "x"')]), "windows") is True
    assert PF.eval_c("defined(_WIN32) || defined(__APPLE__)", "macos") is True
    assert PF.eval_c("defined(_WIN32) || defined(__APPLE__)", "linux") is False
    assert PF.eval_c("defined(__linux__) && HAVE_X", "linux") is None
    assert PF.eval_c("defined(__linux__) && HAVE_X", "windows") is False
    assert PF.eval_atom("dart_library", "io", "web") is False and PF.eval_atom("dart_library", "html", "web") is True
    assert PF.resolve_platform("Win32") == "windows" and PF.resolve_platform("darwin") == "macos"
    with pytest.raises(ValueError, match="known targets"):
        PF.resolve_platform("beos")


def test_scanner_regions():
    from codegraph.platform_scan import scan
    src = ("if (Platform.OS === 'ios') { a() } else if (Platform.OS === 'android') { b() } else { c() }\n"
           "const s = 'Platform.OS === \"web\"' // Platform.OS === 'web'\n")
    regs = scan(src, "ts")
    texts = [r[5] for r in regs]
    assert any("ios" in t for t in texts) and any("android" in t for t in texts)
    assert len(regs) == 3                       # if / else-if / else; strings and comments are masked
    els = next(r for r in regs if r[5].startswith("else of"))
    assert PF.eval_tree(els[4], "web") is True and PF.eval_tree(els[4], "ios") is False
    # no semicolons: the single-statement body of an early return ends at its newline (ASI)
    guard = scan("function f() {\n  if (Platform.OS !== 'ios') return\n  native.call(x)\n}\n", "ts")
    assert len(guard) == 2 and guard[0][0] == guard[0][2] == 2
    # ... and the rest of the function runs only where the guard is false (ios)
    rest = guard[1]
    assert (rest[0], rest[2]) == (2, 4) and PF.eval_tree(rest[4], "ios") is True and PF.eval_tree(rest[4], "android") is False
    assert len(scan("function f() {\n  if (Platform.OS === 'ios') { a() }\n  b()\n}\n", "ts")) == 1
    multi = scan("if (Platform.OS === 'ios')\n  a\n    .b()\nc()\n", "ts")
    assert len(multi) == 1 and (multi[0][0], multi[0][2]) == (2, 3)


# ------------------------------------------------------------------ Rust
@needs_ts
def test_rust_cfg_tags_variants_and_filtered_impact():
    st, res = graph("rust_app", CODEGRAPH_RUST_SCIP="0")
    s = res["platforms"]
    assert s["targets"] == ["windows", "linux", "macos"]
    assert tags(st, "function:dirs_demo::paths::config_dir") == ["windows"]
    assert tags(st, "function:dirs_demo::paths::config_dir@9") == ["linux", "macos", "ios", "android"]
    assert tags(st, "mod:dirs_demo::win") == ["windows"] and tags(st, "function:dirs_demo::win::set_console_title") == ["windows"]
    assert tags(st, "function:dirs_demo::dock_badge") == ["macos"]
    assert tags(st, "function:dirs_demo::main") is None
    # `if cfg!(target_os = "macos") { dock_badge() } else { tray_hint() }`: the calls are tagged, not the callees
    e = st.q("SELECT attrs FROM edges WHERE src='function:dirs_demo::main' AND dst='function:dirs_demo::tray_hint' AND kind='CALLS'")
    assert "macos" not in json.loads(e[0]["attrs"])["platforms"]
    # both config_dir variants are callers' targets: the call reaches the unix one too
    assert "function:dirs_demo::main" in callers(st, "function:dirs_demo::paths::config_dir@9")
    assert "function:dirs_demo::main" in callers(st, "function:dirs_demo::paths::config_dir@9", "linux")
    assert callers(st, "function:dirs_demo::paths::config_dir@9", "windows") == set()
    assert callers(st, "function:dirs_demo::dock_badge", "linux") == set()
    assert callers(st, "function:dirs_demo::tray_hint", "macos") == set()
    assert "function:dirs_demo::main" in callers(st, "function:dirs_demo::tray_hint", "linux")
    d = s["divergence"]
    assert any(v["name"] == "dirs_demo::paths::config_dir" and not v["missing"] for v in d["variants"])
    mc = [m for m in d["missing_callee"] if m["to"] == "function:dirs_demo::open_logs"]
    assert mc and mc[0]["missing_on"] == ["windows", "macos"]


@needs_ts
def test_rust_scip_mode_keeps_inactive_cfg_calls():
    from native_util import rust_analyzer
    if not rust_analyzer():
        pytest.skip("rust-analyzer not installed")
    st, res = graph("rust_app")
    # rust-analyzer only sees the host's cfg: calls into code for other targets are resolved syntactically
    for dst in ("function:dirs_demo::paths::config_dir", "function:dirs_demo::win::set_console_title"):
        assert st.q("SELECT 1 FROM edges WHERE src='function:dirs_demo::main' AND dst=? AND kind='CALLS'", (dst,)), dst
    assert any(m["to"] == "function:dirs_demo::open_logs" for m in res["platforms"]["divergence"]["missing_callee"])


# ------------------------------------------------------------------ C
@needs_ts
def test_c_ifdef_variants_and_unknown_conditions():
    st, res = graph("c_app")
    s = res["platforms"]
    assert "android" in s["targets"] and s["target_sources"]["android"].startswith("named by")
    assert tags(st, "function:sleep_ms@src/clock.c:4") == ["windows"]
    assert tags(st, "function:src/clock.c#vibrate") == ["android"]
    # `sleep_ms(...)` from main resolves to both definitions
    main = st.q("SELECT id FROM nodes WHERE kind='function' AND name='main'")[0]["id"]
    dsts = {r["dst"] for r in st.q("SELECT dst FROM edges WHERE src=? AND kind='CALLS'", (main,))}
    assert {"function:sleep_ms@src/clock.c:4", "function:sleep_ms@src/clock.c:8"} <= dsts
    assert callers(st, "function:sleep_ms@src/clock.c:4", "linux") == set()
    assert main in callers(st, "function:sleep_ms@src/clock.c:4", "windows")
    assert s["unevaluated_conditions"] == 1 and "HAVE_SOUND" in s["unevaluated_samples"][0]
    v = next(v for v in s["divergence"]["variants"] if v["name"] == "sleep_ms")
    assert v["missing"] == [] and set(v["covered"]) == {"windows", "linux", "macos", "android"}
    info = PF.filter_info(st, "windows")
    assert info["unevaluated_conditions"] == 1 and info["excluded_nodes"] >= 3


# ------------------------------------------------------------------ Dart / Flutter
@needs_dart
def test_dart_conditional_imports_and_platform_checks():
    st, res = graph("dart_app")
    s = res["platforms"]
    assert s["targets"] == ["ios", "android", "web"] and s["target_sources"]["web"] == "Flutter folder web/"
    assert tags(st, "module:lib/storage/storage_web.dart") == ["web"]
    assert "web" not in tags(st, "function:lib/storage/storage_io.dart#cachePath")
    imp = st.q("SELECT attrs FROM edges WHERE kind='IMPORTS' AND src='module:lib/sync.dart' AND dst='module:lib/storage/storage_web.dart'")
    assert imp and json.loads(imp[0]["attrs"]).get("conditional")
    d = s["divergence"]
    api = [a for a in d["api_surface"] if a["symbol"] == "cachePath"]
    assert api and api[0]["missing_on"] == ["web"]
    assert any(m["from"] == "function:lib/sync.dart#syncAll" and m["missing_on"] == ["web"] for m in d["missing_callee"])
    # filtered impact: on web, save() resolves to the web library only
    web = callers(st, "function:lib/storage/storage_web.dart#save", "web")
    assert "function:lib/sync.dart#syncAll" in web
    assert callers(st, "function:lib/storage/storage_web.dart#save", "ios") == set()
    assert "function:lib/sync.dart#syncAll" in callers(st, "function:lib/storage/storage_io.dart#save", "android")
    # kIsWeb ? null : (Platform.isIOS ? iosPath() : androidPath())
    assert "function:lib/paths.dart#appPath" in callers(st, "function:lib/paths.dart#iosPath", "ios")
    assert callers(st, "function:lib/paths.dart#iosPath", "android") == set()
    assert callers(st, "function:lib/paths.dart#iosPath", "web") == set()
    assert callers(st, "function:lib/paths.dart#desktopSettings", "ios") == set()
    assert "function:lib/paths.dart#openSettings" in callers(st, "function:lib/paths.dart#desktopSettings", "linux")


# ------------------------------------------------------------------ React Native / Expo
def test_react_native_platform_files_and_platform_os():
    st, res = graph("rn_app")
    s = res["platforms"]
    assert s["targets"] == ["ios", "android", "web"] and s["target_sources"]["ios"] == "app.json expo.platforms"
    assert tags(st, "module:src/storage/storage.ios.ts") == ["ios"]
    assert tags(st, "module:src/haptics.ts") == ["web"]
    assert "web" not in tags(st, "function:src/haptics.native.ts#tap")
    # the import resolves to storage.ios.ts (moduleSuffixes); the android variant is linked too
    assert "function:src/sync.ts#syncNotes" in callers(st, "function:src/storage/storage.android.ts#save", "android")
    assert callers(st, "function:src/storage/storage.android.ts#save", "ios") == set()
    assert "function:src/sync.ts#syncNotes" in callers(st, "function:src/haptics.ts#tap", "web")
    assert "function:src/sync.ts#syncNotes" in callers(st, "function:src/haptics.native.ts#tap", "ios")
    assert callers(st, "function:src/haptics.ts#tap", "ios") == set()
    assert "function:src/screens/Settings.tsx#openSettings" in callers(st, "function:src/screens/Settings.tsx#openIosSettings", "ios")
    assert callers(st, "function:src/screens/Settings.tsx#openIosSettings", "android") == set()
    assert tags(st, "function:src/screens/Settings.tsx#android") == ["android"]
    d = s["divergence"]
    v = next(v for v in d["variants"] if v["name"] == "src/storage/storage")
    assert v["missing"] == ["web"] and v["used_at"] == ["src/sync.ts:1"]
    assert not d["api_surface"]                 # keychainWrite / vibrateNative are private helpers
    assert any(m["missing_on"] == ["web"] and m["to"].startswith("function:src/storage/") for m in d["missing_callee"])
    assert PF.divergence(st, target="ios")["missing_callee"] == []


# ------------------------------------------------------------------ queries without --platform are unchanged
def test_unfiltered_queries_unchanged():
    st, _ = graph("rn_app")
    for spec in ("function:src/storage/storage.ios.ts#save", "function:src/haptics.ts#tap", "function:src/screens/Settings.tsx#openIosSettings"):
        a, b = Q.impact(st, spec), Q.impact(st, spec, platform=None)
        assert a == b and "platform" not in a
    r = Q.reaches(st, ["function:src/storage/storage.ios.ts#save"])
    assert "platform" not in r and {i["id"] for i in r["items"]} >= {"function:src/sync.ts#syncNotes"}
    # a graph without platform code: the filter is a no-op and says so
    st2, res2 = graph("rust_app", CODEGRAPH_RUST_SCIP="0")
    assert PF.filter_info(st2, "ios")["note"].startswith("ios is not a declared target")


def test_filtered_search_routes_path_downstream():
    st, _ = graph("rn_app")
    names = {n["id"] for n in Q.search(st, "save", platform="web")["nodes"]}
    assert not any("storage." in n for n in names)
    r = Q.search(st, "save", platform="web")
    assert r["platform"]["matches_not_built"] >= 2
    assert Q.path_between(st, "function:src/sync.ts#syncNotes", "function:src/storage/storage.android.ts#save", platform="ios") == []
    assert Q.path_between(st, "function:src/sync.ts#syncNotes", "function:src/storage/storage.android.ts#save", platform="android")
    d = Q.downstream(st, "function:src/sync.ts#syncNotes", platform="web")
    assert d["platform"]["platform"] == "web"
    from codegraph.routes import routes_report
    assert routes_report(st, platform="web")["platform"]["platform"] == "web"


# ------------------------------------------------------------------ CLI, MCP, config
def test_cli_platform_flags():
    graph("rn_app")
    db = str(_C[("rn_app", ())][2])
    p = cli("impact", "function:src/storage/storage.ios.ts#save", "--db", db, "--platform", "web")
    assert p.returncode == 0 and "platform: web" in p.stdout and "not built for web" in p.stdout
    p = cli("impact", "x", "--db", db, "--platform", "beos")
    assert p.returncode == 2 and "known targets" in p.stderr
    p = cli("platforms", "divergence", "--db", db, "--target", "web")
    assert p.returncode == 0 and "missing: web" in p.stdout
    p = cli("platforms", "--db", db)
    assert "app.json expo.platforms" in p.stdout
    p = cli("coverage", "--db", db)
    assert "platforms: ios" in p.stdout


def test_mcp_platform_replies():
    import codegraph.mcp_server as M
    graph("rn_app")
    old = M.STATE["db"]
    M.STATE["db"] = str(_C[("rn_app", ())][2])
    try:
        txt = M.impact("function:src/haptics.native.ts#tap", platform="ios")
        assert txt.startswith("platform: ios") and "could not be evaluated" in txt
        sc = M.impact.structured("function:src/haptics.native.ts#tap", platform="ios")
        assert sc["platform"]["platform"] == "ios" and sc["platform"]["unevaluated_conditions"] == 0
        assert "platform" not in M.impact.structured("function:src/haptics.native.ts#tap")
        assert M.reaches(["function:src/haptics.ts#tap"], platform="web").startswith("platform: web")
        assert M.search("save", platform="ios").startswith("platform: ios")
        assert "missing: web" in M.platform_divergence()
        assert "app.json" in M.platforms()
        assert M.path("function:src/sync.ts#syncNotes", "function:src/haptics.ts#tap", platform="beos").startswith("platform error:")
    finally:
        M.STATE["db"] = old


def test_config_platforms_section(tmp_path):
    c = parse({"platforms": {"targets": ["iOS", "android"], "paths": {"src/win/**": ["win32"], "src/posix/**": "unix"},
                             "file_suffixes": False}})
    assert c["platforms"] == {"targets": ["ios", "android"], "paths": {"src/win/**": ["windows"], "src/posix/**": ["unix"]},
                              "file_suffixes": False}
    for bad in ({"targets": ["beos"]}, {"paths": ["x"]}, {"paths": {"../x": ["linux"]}}, {"file_suffixes": "no"},
                {"colour": 1}):
        with pytest.raises(ConfigError):
            parse({"platforms": bad})
    # .cg.yaml targets and paths drive the index
    (tmp_path / "src" / "win").mkdir(parents=True)
    (tmp_path / "src" / "win" / "io.ts").write_text("export function openPort(): void {}\n")
    (tmp_path / "src" / "main.ts").write_text("import { openPort } from './win/io'\nexport function run(): void { openPort() }\n")
    (tmp_path / "package.json").write_text('{"name": "x"}\n')
    (tmp_path / "tsconfig.json").write_text('{"compilerOptions": {"strict": true}}\n')
    (tmp_path / ".cg.yaml").write_text("platforms:\n  targets: [windows, linux]\n  paths:\n    'src/win/**': [windows]\n")
    db, res = index(tmp_path)
    st = GraphStore(str(db))
    assert res["platforms"]["targets"] == ["windows", "linux"]
    assert tags(st, "function:src/win/io.ts#openPort") == ["windows"]
    assert callers(st, "function:src/win/io.ts#openPort", "linux") == set()
    assert "function:src/main.ts#run" in callers(st, "function:src/win/io.ts#openPort", "windows")
    assert any(m["to"] == "function:src/win/io.ts#openPort" and m["missing_on"] == ["linux"]
               for m in res["platforms"]["divergence"]["missing_callee"])


def test_declared_targets_swiftpm_kmp_tauri(tmp_path):
    sp = tmp_path / "spm"
    sp.mkdir()
    (sp / "Package.swift").write_text('let package = Package(name: "x",\n platforms: [\n .iOS(.v15),\n .tvOS(.v15),\n'
                                      ' .macOS(.v12)\n ], targets: [])\n')
    t, src = PF.declared_targets(sp, {}, [], {"swift"})
    assert t == ["macos", "ios"] and src["ios"] == "Package.swift platforms: .iOS"
    kmp = tmp_path / "kmp" / "shared"
    kmp.mkdir(parents=True)
    (kmp / "build.gradle.kts").write_text('''plugins { kotlin("multiplatform") }
kotlin {
    androidTarget { }
    // jvm("desktop")
    listOf(iosX64(), iosArm64(), iosSimulatorArm64()).forEach { }
    wasmJs { browser() }
    sourceSets { commonMain.dependencies { } }
}
''')
    t, src = PF.declared_targets(kmp.parent, {}, [], {"kotlin"})
    assert t == ["ios", "android", "web"], t
    assert src["android"] == "shared/build.gradle.kts kotlin { androidTarget() }"
    (kmp / "build.gradle.kts").write_text('plugins { id("org.jetbrains.kotlin.multiplatform") }\nkotlin { jvm("desktop")\n'
                                          'iosArm64() }\n')
    assert PF.declared_targets(kmp.parent, {}, [], {"kotlin"})[0] == ["windows", "linux", "macos", "ios"]
    (kmp / "build.gradle.kts").write_text('plugins { alias(libs.plugins.kotlin.multiplatform) }\nkotlin { jvm()\n'
                                          'androidNativeArm64(); mingwX64() }\n')
    assert PF.declared_targets(kmp.parent, {}, [], {"kotlin"})[0] == ["windows", "android"]
    ta = tmp_path / "tauri"
    (ta / "src-tauri" / "gen" / "android").mkdir(parents=True)
    (ta / "src-tauri" / "tauri.conf.json").write_text("{}")
    t, src = PF.declared_targets(ta, {}, [], {"ts", "rust"})
    assert t == ["windows", "linux", "macos", "android"] and src["android"] == "Tauri mobile project src-tauri/gen/android/"
