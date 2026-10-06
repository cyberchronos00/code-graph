"""Platform follow-ups of #21 (#56): exact rust-analyzer runs per target, Swift availability, re-exports in variant
files (TS / Dart), C macro-generated functions and definitions after an unparsable region, Rust #[path] files."""
import json
import sqlite3
import textwrap
from pathlib import Path

import pytest

from native_util import ROOT, TS_SKIP, have_tree_sitter, index, rust_analyzer, stats_of
import sys

sys.path.insert(0, str(ROOT))
from cg_code_graph import platforms as PF  # noqa: E402
from cg_code_graph.core.store import GraphStore  # noqa: E402
from cg_code_graph.plugins.dart.plugin import find_dart  # noqa: E402

needs_ts = pytest.mark.skipif(not have_tree_sitter(), reason=TS_SKIP)


def write(root: Path, files: dict) -> Path:
    for rel, body in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(textwrap.dedent(body).lstrip("\n"))
    return root


def rows(db, sql, *args):
    c = sqlite3.connect(str(db))
    try:
        return c.execute(sql, args).fetchall()
    finally:
        c.close()


def attrs(db, nid):
    r = rows(db, "select attrs from nodes where id=?", nid)
    assert r, nid
    return json.loads(r[0][0] or "{}")


def findings(db):
    return PF.divergence(GraphStore(str(db)))


RUST_CFG = {
    "Cargo.toml": """
        [package]
        name = "ra"
        version = "0.1.0"
        edition = "2021"
    """,
    "src/main.rs": """
        fn helper() -> u32 { 1 }
        fn other() -> u32 { 2 }
        #[cfg(windows)]
        fn win_only() -> u32 { helper() }
        #[cfg(target_os = "macos")]
        fn mac_only() -> u32 { other() }
        #[path = "../shared/extra.rs"]
        mod extra;
        fn main() {
            #[cfg(windows)]
            win_only();
            #[cfg(target_os = "macos")]
            mac_only();
            helper();
            extra::shared();
        }
    """,
    "shared/extra.rs": """
        pub fn shared() {}
    """,
}


@needs_ts
def test_rust_path_attr_normalised(tmp_path):
    db, res = index(write(tmp_path, RUST_CFG), CG_RUST_SCIP="0")
    files = {r[0] for r in rows(db, "select distinct file from nodes where lang='rust'")}
    assert "shared/extra.rs" in files and not any(".." in f for f in files if f)
    assert rows(db, "select 1 from edges where src='function:ra::main' and dst='function:ra::extra::shared'")
    assert stats_of(res, "rust").get("orphan_rs_files", 0) == 0


@pytest.mark.skipif(not have_tree_sitter() or not rust_analyzer(), reason="rust-analyzer / tree-sitter not installed")
def test_rust_exact_index_per_target(tmp_path):
    db, res = index(write(tmp_path, RUST_CFG), CG_RUST_SCIP=None, CG_RUST_TARGETS=None)
    st = stats_of(res, "rust")
    assert st["mode"] == "scip"
    tg = st["scip"]["targets"]
    assert set(tg) == {"windows", "macos"} and tg["windows"]["triple"] == "x86_64-pc-windows-msvc"
    e = rows(db, "select confidence, attrs from edges where src='function:ra::main' and dst='function:ra::win_only' and kind='CALLS'")
    assert e and e[0][0] == "exact"
    a = json.loads(e[0][1])
    assert a["exact_target"] == "windows" and a["platforms"] == ["windows"] and "via" not in a
    e = rows(db, "select confidence, attrs from edges where src='function:ra::main' and dst='function:ra::mac_only'")
    assert e and e[0][0] == "exact" and json.loads(e[0][1])["exact_target"] == "macos"
    # opt out: the syntactic fallback for code under an inactive cfg
    db2, res2 = index(tmp_path, CG_RUST_SCIP=None, CG_RUST_TARGETS="0")
    assert "targets" not in stats_of(res2, "rust")["scip"]
    a = json.loads(rows(db2, "select attrs from edges where src='function:ra::main' and dst='function:ra::win_only'")[0][0])
    assert a["via"] == "cfg-inactive" and "exact_target" not in a


SWIFT = {
    "Package.swift": """
        // swift-tools-version:5.9
        import PackageDescription
        let package = Package(name: "App", platforms: [.iOS(.v16), .macOS(.v13)], targets: [.executableTarget(name: "App")])
    """,
    "Sources/App/main.swift": """
        @available(iOS 17, macOS 14, *)
        func newApi() {}
        func old() {}
        func later() {}

        @available(iOS, introduced: 15.0, deprecated: 17.0, message: "use newApi")
        func legacy() {}

        @available(iOS 16.4, *)
        struct Box {
          func open() {}
          @available(iOS 17, *)
          func peek() {}
        }

        @available(*, unavailable)
        func gone() {}

        func f() {
          if #available(iOS 17, *) {
            newApi()
          } else {
            old()
          }
          guard #available(iOS 16, *) else { return }
          later()
        }
    """,
}


@needs_ts
def test_swift_availability(tmp_path):
    db, res = index(write(tmp_path, SWIFT), CG_SWIFT_INDEX="0")
    assert attrs(db, "function:newApi")["available"] == {"iOS": "17", "macOS": "14"}
    leg = attrs(db, "function:legacy")
    # the package deploys iOS 16 / macOS 13 (#100): iOS 15.0 always holds, so only the declared form is kept
    assert "available" not in leg and leg["available_declared"] == {"iOS": "15.0"} and leg["deprecated"] == "use newApi"
    assert attrs(db, "method:Box.open")["available"] == {"iOS": "16.4"}          # inherited from the struct
    assert attrs(db, "method:Box.peek")["available"] == {"iOS": "17"}            # the newer of the two
    assert attrs(db, "function:gone")["platforms"] == []                         # @available(*, unavailable)
    assert "platforms" not in attrs(db, "function:newApi")                       # versions do not drop a target
    e = dict(rows(db, "select dst, attrs from edges where src='function:f' and kind='CALLS'"))
    assert json.loads(e["function:newApi"])["available"] == {"iOS": "17"}
    assert "available" not in json.loads(e["function:old"] or "{}")
    assert "available" not in json.loads(e["function:later"] or "{}")             # guard #available(iOS 16): met
    sw = json.loads(rows(db, "select value from meta where key='stats'")[0][0])["plugins"]["swift"]
    assert sw["deployment_targets"] == {"iOS": "16", "macOS": "13"}
    assert [(x["check"], x["requires"]) for x in sw["availability_always_true"]] == [
        ("@available", "iOS 15.0"), ("#available", "iOS 16")]
    assert PF.label({"available": {"iOS": "17"}}) == "  [iOS 17+]"


TS_VARIANTS = {
    "package.json": '{"name":"rn","dependencies":{"react-native":"0.74.0"}}',
    "tsconfig.json": '{"compilerOptions":{"strict":true,"moduleSuffixes":[".ios",".android",".web",""]},"include":["src"]}',
    "src/shared.ts": """
        export function load(k: string) { return k; }
        export function clear() {}
    """,
    "src/storage.ios.ts": """
        export function save(k: string) { return k; }
        export function load(k: string) { return k; }
        export function clear() {}
        export function batch(f: () => void) { f(); }
    """,
    "src/storage.android.ts": """
        export function save(k: string) { return k; }
        export { load, clear } from './shared';
        export { unstable_batchedUpdates as batch } from 'react-dom';
    """,
    "src/storage.web.ts": """
        import { load as l, clear } from './shared';
        import { unstable_batchedUpdates } from 'react-dom';
        export function save(k: string) { return k; }
        export { l as load, clear };
        export const batch = unstable_batchedUpdates;
    """,
    "src/app.ts": """
        import { save, load, clear, batch } from './storage';
        export function run() { save('a'); load('b'); clear(); batch(() => {}); }
    """,
}


@needs_ts
def test_ts_reexports_in_variant_files(tmp_path):
    db, _ = index(write(tmp_path, TS_VARIANTS))
    a = attrs(db, "module:src/storage.android.ts")
    assert a["reexports"] == {"load": "function:src/shared.ts#load", "clear": "function:src/shared.ts#clear"}
    assert a["reexports_external"] == ["batch"]
    w = attrs(db, "module:src/storage.web.ts")
    assert w["reexports"] == {"load": "function:src/shared.ts#load", "clear": "function:src/shared.ts#clear"}
    assert w["reexports_external"] == ["batch"]
    assert rows(db, "select 1 from edges where src='module:src/storage.android.ts' and dst='module:src/shared.ts' and kind='IMPORTS'")
    # the call through the variant reaches the re-exported definition
    e = rows(db, "select attrs from edges where src='function:src/app.ts#run' and dst='function:src/shared.ts#load'")
    assert e and json.loads(e[0][0])["platform_variant_of"] == "function:src/storage.ios.ts#load"
    d = findings(db)
    assert d["api_surface"] == [] and d["missing_callee"] == [], d


DART_VARIANTS = {
    "pubspec.yaml": "name: dv\nenvironment:\n  sdk: \">=3.0.0 <4.0.0\"\n",
    "lib/storage.dart": "export 'storage_stub.dart' if (dart.library.io) 'storage_io.dart' if (dart.library.js_interop) 'storage_web.dart';\n",
    "lib/storage_stub.dart": """
        String save(String k) => throw UnimplementedError();
        String load(String k) => throw UnimplementedError();
        void clear() {}
    """,
    "lib/storage_io.dart": """
        import 'src/io_impl.dart';
        export 'src/io_impl.dart' show load;
        String save(String k) => k;
        const clear = IoStore.clear;
    """,
    "lib/src/io_impl.dart": """
        String load(String k) => k;
        class IoStore {
          static void clear() {}
        }
    """,
    "lib/storage_web.dart": """
        String save(String k) => k;
        String load(String k) => k;
        void clear() {}
    """,
    "lib/main.dart": """
        import 'storage.dart';
        void run() { save('a'); load('b'); clear(); }
    """,
}


@pytest.mark.skipif(find_dart() is None, reason="Dart SDK not found (set $DART or put dart on PATH)")
def test_dart_reexports_and_tearoffs_in_variant_files(tmp_path):
    db, _ = index(write(tmp_path, DART_VARIANTS))
    a = attrs(db, "module:lib/storage_io.dart")
    assert a["reexports"] == {"load": "function:lib/src/io_impl.dart#load", "clear": "method:lib/src/io_impl.dart#IoStore.clear"}
    assert rows(db, "select 1 from edges where src='module:lib/storage_io.dart' and dst='module:lib/src/io_impl.dart' and kind='IMPORTS'")
    d = findings(db)
    assert d["api_surface"] == [] and d["missing_callee"] == [], d


C_SRC = {
    "a.c": r"""
        #include <stdio.h>
        #ifdef _WIN32
        # define SEP '\\'  /* windows */
        # define REQ_INIT(r)                 \
          do {                               \
            (r)->x = 0;  /* comment */       \
          }                                  \
          while (0)
        #else
        # define SEP '/'
        # define REQ_INIT(r) do { (r)->x = 1; } while (0)
        #endif

        #define DEFINE_GETTER(name) int get_##name(const struct box* b) { return b->name; }
        #define DEFINE_SETTER(name, field)   \
          static void set_##name(struct box* b, int v) { b->field = v; }
        struct box { int width; int height; int x; };
        DEFINE_GETTER(width)
        DEFINE_GETTER(height)
        DEFINE_SETTER(width,
                      width)

        int area(struct box* b) { REQ_INIT(b); return get_width(b) * get_height(b); }

        int broken(void) {
          return FOO(BAR BAZ ;; } }} ]
        }

        int after_broken(struct box* b) { set_width(b, 2); return area(b) + SEP; }

        int main(void) { struct box b; printf("%d", after_broken(&b)); return 0; }
    """,
}


@needs_ts
def test_c_macro_generated_and_recovered_definitions(tmp_path):
    db, res = index(write(tmp_path, C_SRC), CG_C_SCIP="0")
    fns = {r[0]: json.loads(r[1] or "{}") for r in rows(db, "select id, attrs from nodes where kind='function'")}
    assert fns["function:get_width"]["macro_generated"] == "DEFINE_GETTER"
    assert fns["function:get_height"]["macro_generated"] == "DEFINE_GETTER"
    assert fns["function:a.c#set_width"]["macro_generated"] == "DEFINE_SETTER"   # static, multi-line expansion
    assert "function:DEFINE_GETTER" not in fns                                    # no bogus function from the expansion
    assert fns["function:after_broken"].get("recovered") and fns["function:main"].get("recovered")
    calls = {(s, d) for s, d in rows(db, "select src, dst from edges where kind='CALLS'")}
    assert ("function:area", "function:get_width") in calls
    assert ("function:after_broken", "function:a.c#set_width") in calls
    assert ("function:after_broken", "function:area") in calls
    assert ("function:main", "function:after_broken") in calls
    # both #if branches of a macro tree-sitter only half-parses are nodes (a variant pair)
    req = sorted(r[0] for r in rows(db, "select id from nodes where name='REQ_INIT'"))
    assert len(req) == 2
    assert stats_of(res, "c_cpp")["macro_generated_functions"] == 3
    assert stats_of(res, "c_cpp")["recovered_definitions"] >= 3


def test_c_platform_directory_pair_is_one_symbol(tmp_path):
    # the same function in src/unix/ and src/win/ (file-qualified keys): a caller elsewhere reaches both definitions
    db, _ = index(write(tmp_path, {
        "include/a.h": "void f(void);\n",
        "src/unix/a.c": '#include "a.h"\nvoid f(void) {\n}\n',
        "src/win/a.c": '#include "a.h"\nvoid f(void) {\n}\n',
        "main.c": '#include "a.h"\nint main(void) {\n  f();\n  return 0;\n}\n',
    }), CG_C_SCIP="0")
    calls = {d for s, d in rows(db, "select src, dst from edges where kind='CALLS' and src='function:main'")}
    assert calls == {"function:src/unix/a.c#f", "function:src/win/a.c#f"}


RN_63 = {
    "package.json": '{"name": "rn63", "dependencies": {"react": "18.2.0", "react-native": "0.74.0", "react-native-web": "~0.19.10"}}',
    "app.json": '{"expo": {"name": "rn63", "platforms": ["ios", "android", "web"]}}',
    "tsconfig.json": '{"compilerOptions": {"strict": true, "module": "esnext", "moduleResolution": "bundler", "target": "es2020"}, "include": ["src"]}',
    "src/x/Cam.ios.ts": "export function snap(): string { return 'ios'; }\n",
    "src/x/Cam.android.ts": "export function snap(): string { return 'android'; }\n",
    "src/useCam.ts": """
        import { snap } from './x/Cam.ios';

        export function iosOnlyDebug() {
          return snap();
        }
        """,
    "src/web.ts": "export function webOnly() { return 1; }\n",
    "src/web.web.ts": "export function webOnly() { return 2; }\nexport function webExtra() { return 3; }\n",
    "src/release.web.test.ts": """
        import { webExtra } from './web';
        test('x', () => { webExtra(); });
        """,
}


def test_ts_explicit_platform_import_and_platform_test_files(tmp_path):
    """#63: `from './x/Cam.ios'` gets that file on every target (no mirrored edge to Cam.android, no missing-callee on
    web); `release.web.test.ts` is a web test, so its call into web-only code is no finding."""
    db, _ = index(write(tmp_path, RN_63))
    assert not rows(db, "select 1 from edges where src like '%useCam.ts%' and dst like '%Cam.android%'")
    assert attrs(db, "module:src/release.web.test.ts")["platforms"] == ["web"]
    d = findings(db)
    assert d["missing_callee"] == [], d["missing_callee"]


def test_swift_initializer_added_to_sdk_type(tmp_path):
    """#63: `Image(systemName:)` bound by name to a project `extension Image { init(systemName:) }` under
    `#if os(macOS)` is not a call into macOS-only code on iOS (the SDK has its own initializer)."""
    pytest.importorskip("tree_sitter_swift")
    db, _ = index(write(tmp_path, {
        "Package.swift": """
            // swift-tools-version:5.9
            import PackageDescription
            let package = Package(name: "App", platforms: [.iOS(.v15), .macOS(.v12)], targets: [.target(name: "App")])
            """,
        "Sources/App/Ext.swift": """
            import SwiftUI

            #if os(macOS)
            extension Image {
                init(systemName: String) {
                    self.init(nsImage: NSImage())
                }
            }
            #endif
            """,
        "Sources/App/V.swift": """
            import SwiftUI

            struct V: View {
                var body: some View {
                    Image(systemName: "star")
                }
            }
            """,
    }))
    assert rows(db, "select 1 from edges where src='method:V.body' and dst='method:Image.init'")
    assert findings(db)["missing_callee"] == []


def test_c_separate_programs_and_callees_on_no_target(tmp_path):
    """#63: same-named functions of separate programs (each file with main()) are no per-platform definition group;
    a call into a definition built for no declared target (a sunos.c fallback) is not listed."""
    db, _ = index(write(tmp_path, {
        "docs/a/main.c": "static int n;\nvoid alloc_buffer(void) {\n}\nint main(void) {\n  alloc_buffer();\n  return 0;\n}\n",
        "docs/b/main.c": "void alloc_buffer(void) {\n}\nint main(void) {\n  alloc_buffer();\n  return 0;\n}\n",
        "test/t.c": "#ifndef _WIN32\nvoid alloc_buffer(void) {\n}\n#endif\nvoid t(void) {\n  alloc_buffer();\n}\n",
        "src/unix/sunos.c": "unsigned long strnlen(const char* s, unsigned long n) {\n  return 0;\n}\n",
        "src/unix/core.c": "unsigned long strnlen(const char* s, unsigned long n);\nvoid use(void) {\n  strnlen(\"a\", 1);\n}\n",
        "src/win/core.c": "void wuse(void) {\n}\n",
    }), CG_C_SCIP="0")
    d = findings(db)
    assert not [v for v in d["variants"] if v["name"] == "alloc_buffer"], d["variants"]
    assert not [m for m in d["missing_callee"] if "strnlen" in m["to"]], d["missing_callee"]
    assert d["counts"].get("missing_callee_skipped_no_target", 0) >= 1
