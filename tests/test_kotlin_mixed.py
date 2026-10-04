"""Mixed Kotlin / Java modules in Kotlin exact mode: the Java documents of the scip-java index the Kotlin plugin
consumes are imported as `java` nodes with exact Kotlin -> Java and Java -> Kotlin edges (the generic SCIP importer
skips a consumed index). tests/kotlin_mixed_fixture/index.scip was produced by scip-java 0.12.3 (Kotlin 2.1.20) from
that directory."""
import os
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
pytest.importorskip("tree_sitter_kotlin")
from codegraph.indexer import index_project  # noqa: E402

FIX = ROOT / "tests" / "kotlin_mixed_fixture"
SCIP = FIX / "index.scip"


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.delenv("CODEGRAPH_KOTLIN_SCIP", raising=False)
    monkeypatch.delenv("CODEGRAPH_KOTLIN_SCIP_FILE", raising=False)
    monkeypatch.setenv("CODEGRAPH_CACHE", str(tmp_path / "cache"))


def _index(tmp_path, **kw):
    db = tmp_path / "g.db"
    st = index_project(FIX, db, "kotlin-mixed", **kw)
    return st, sqlite3.connect(db)


def _edges(con, kind):
    return {(s, d): c for s, d, c in con.execute("select src, dst, confidence from edges where kind=?", (kind,))}


def _cov(st, lang):
    return next(e for e in st["coverage"]["languages"] if e["language"] == lang)


def test_java_documents_imported(tmp_path, isolated):
    st, con = _index(tmp_path, scip=[str(SCIP)])
    k = st["plugins"]["kotlin"]
    assert k["mode"] == "scip"
    assert k["java"]["documents"] == 1
    assert k["java"]["classes"] == 2 and k["java"]["methods"] == 5
    nodes = {i: (kind, lang) for i, kind, lang in con.execute("select id, kind, lang from nodes")}
    for nid in ("class:demo.Formatter", "class:demo.Formatter.Box", "method:demo.Formatter::bold",
                "method:demo.Formatter.Box::make", "file:java:src/main/java/demo/Formatter.java"):
        assert nodes[nid][1] == "java", nid
    assert not any(i.endswith("::<init>") for i in nodes)
    contains = _edges(con, "CONTAINS")
    assert ("class:demo.Formatter", "class:demo.Formatter.Box") in contains
    assert ("class:demo.Formatter.Box", "method:demo.Formatter.Box::make") in contains


def test_cross_language_edges_exact(tmp_path, isolated):
    _, con = _index(tmp_path, scip=[str(SCIP)])
    calls, inst = _edges(con, "CALLS"), _edges(con, "INSTANTIATES")
    # Kotlin -> Java
    assert calls[("function:demo.summary", "method:demo.Formatter::bold")] == "exact"
    assert calls[("function:demo.summary", "method:demo.Formatter::area")] == "exact"
    assert inst[("function:demo.summary", "class:demo.Formatter")] == "exact"
    # Java -> Kotlin (interface method, overloaded method, constructors, a top-level function through its facade)
    assert calls[("method:demo.Formatter::area", "method:demo.Shape.area")] == "exact"
    assert calls[("method:demo.Formatter::fresh", "method:demo.Registry.add")] == "exact"
    assert inst[("method:demo.Formatter::fresh", "class:demo.Registry")] == "exact"
    assert inst[("method:demo.Formatter::fresh", "class:demo.Circle")] == "exact"
    assert calls[("method:demo.Formatter::built", "function:demo.build")] == "exact"
    # Java -> Java, the caller being the innermost method (a nested class's)
    assert calls[("method:demo.Formatter::area", "method:demo.Formatter::bold")] == "exact"
    assert inst[("method:demo.Formatter.Box::make", "class:demo.Formatter")] == "exact"


def test_coverage_reports_java_imported(tmp_path, isolated):
    st, _ = _index(tmp_path, scip=[str(SCIP)])
    j = _cov(st, "java")
    assert j["status"] == "scip" and "Kotlin exact mode" in j["reason"]
    assert _cov(st, "kotlin")["status"] == "exact"
    assert st["plugins"]["kotlin"]["scip"]["source"] == "--scip"


def test_without_index_java_stays_unsupported(tmp_path, isolated, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.delenv("JAVA_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    st, con = _index(tmp_path)
    assert st["plugins"]["kotlin"]["mode"] == "heuristic"
    assert _cov(st, "java")["status"] == "unsupported"
    assert not con.execute("select count(*) from nodes where lang='java'").fetchone()[0]


# --- Kotlin 2.2+: scip-java 0.13 (SCIP 0.9 typed ranges) and the choice between installed scip-java releases ---

SCIP22 = FIX / "index-kotlin-2.2.scip"   # scip-java 0.13.1 on the same sources with `kotlin("jvm") version "2.2.10"`


def test_scip_java_013_index_typed_ranges(tmp_path, isolated):
    """scip-java 0.13 writes `single_line_range` / `*_enclosing_range` instead of the packed ranges: same graph."""
    from codegraph.plugins.native import scipread
    idx = scipread.load(SCIP22)
    assert idx.docs["src/main/kotlin/demo/Shapes.kt"].occs[1].line == 2      # not all zero
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    _, con21 = _index(tmp_path / "a", scip=[str(SCIP)])
    st22, con22 = _index(tmp_path / "b", scip=[str(SCIP22)])
    q = "select src, dst, kind, confidence from edges"
    assert set(con22.execute(q)) == set(con21.execute(q))
    k = st22["plugins"]["kotlin"]
    assert k["mode"] == "scip" and k["scip_defs_unmatched"] == 0 and k["java"]["references"] == 7


def test_generic_importer_typed_ranges(tmp_path):
    from codegraph.core.plugin import GraphBuilder
    from codegraph.plugins.scip.importer import import_scip
    b = GraphBuilder()
    st = import_scip(SCIP22, b, lang="java")
    assert st["definitions"] and st["references"]
    n = b.nodes["method:demo.Formatter::bold"]
    assert n.line == 4 and n.file == "src/main/java/demo/Formatter.java"
    assert any(e.src == "method:demo.Formatter::area" and e.dst == "method:demo.Formatter::bold"
               for e in b.edges.values())


def test_kotlin_version_detection(tmp_path):
    from codegraph.plugins.kotlin.exact import kotlin_version
    assert kotlin_version(FIX) == (2, 1, 20)
    (tmp_path / "gradle").mkdir()
    (tmp_path / "build.gradle.kts").write_text("plugins { alias(libs.plugins.kotlin.android) apply false }\n")
    (tmp_path / "gradle" / "libs.versions.toml").write_text('[versions]\nagp = "8.9.0"\nkotlin = "2.2.10"\n')
    assert kotlin_version(tmp_path) == (2, 2, 10)
    (tmp_path / "build.gradle.kts").write_text('plugins { id("org.jetbrains.kotlin.jvm") version "2.4.20" }\n')
    assert kotlin_version(tmp_path) == (2, 4, 20)
    p = tmp_path / "pom"
    p.mkdir()
    (p / "pom.xml").write_text("<project><properties><kotlin.version>1.9.25</kotlin.version></properties></project>")
    assert kotlin_version(p) == (1, 9, 25)
    assert kotlin_version(tmp_path / "missing") is None


def _fake(path: Path, gen13: bool, body: str) -> Path:
    path.write_text("#!/bin/sh\n"
                    f'[ "$1" = --help ] && {{ echo "{"aggregate" if gen13 else "index"}"; exit 0; }}\n'
                    '[ "$1" = version ] && { echo test; exit 0; }\n' + body)
    path.chmod(0o755)
    return path


def _emit(scip: Path, log: Path, name: str) -> str:
    return (f'echo {name} >> "{log}"\n'
            'for a in "$@"; do case "$a" in --output=*) cp "' + str(scip) + '" "${a#--output=}";; esac; done\n'
            "exit 0\n")


MISMATCH = ('echo "e: PluginProcessingError: Plugin org.scip_code.scip_java.kotlinc.AnalyzerRegistrar is incompatible '
            'with the current version of the compiler." >&2\nexit 1\n')


@pytest.fixture
def kotlin22(tmp_path, isolated, monkeypatch):
    """A copy of the mixed fixture declaring Kotlin 2.2.10, a `java` on PATH, opted in."""
    import shutil
    proj = tmp_path / "proj"
    shutil.copytree(FIX, proj)
    bf = proj / "build.gradle.kts"
    bf.write_text(bf.read_text().replace("2.1.20", "2.2.10"))
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    (bin_ / "java").write_text("#!/bin/sh\nexit 0\n")
    (bin_ / "java").chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_}{os.pathsep}/usr/bin{os.pathsep}/bin")
    monkeypatch.delenv("JAVA_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("CODEGRAPH_KOTLIN_SCIP", "1")
    monkeypatch.delenv("CODEGRAPH_NO_CACHE", raising=False)
    return proj


def test_scip_java_release_chosen_by_kotlin_version(tmp_path, kotlin22, monkeypatch):
    log = tmp_path / "runs.log"
    old = _fake(tmp_path / "scip-java", False, MISMATCH)
    new = _fake(tmp_path / "scip-java-0.13", True, _emit(SCIP22, log, "new"))
    monkeypatch.setenv("CODEGRAPH_SCIP_JAVA", f"{old}{os.pathsep}{new}")
    st = index_project(kotlin22, tmp_path / "g.db", "k22")
    s = st["plugins"]["kotlin"]["scip"]
    assert st["plugins"]["kotlin"]["mode"] == "scip"
    assert s["indexer"] == str(new) and s["kotlin_version"] == "2.2.10" and "attempts" not in s
    assert log.read_text().split() == ["new"]


def test_scip_java_falls_back_on_plugin_mismatch(tmp_path, kotlin22, monkeypatch):
    log = tmp_path / "runs.log"
    # both claim the 0.13 generation: the first one's compiler plugin does not load, the second one runs
    a = _fake(tmp_path / "scip-java-a", True, MISMATCH)
    b = _fake(tmp_path / "scip-java-b", True, _emit(SCIP22, log, "b"))
    monkeypatch.setenv("CODEGRAPH_SCIP_JAVA", f"{a}{os.pathsep}{b}")
    st = index_project(kotlin22, tmp_path / "g.db", "k22")
    s = st["plugins"]["kotlin"]["scip"]
    assert st["plugins"]["kotlin"]["mode"] == "scip" and s["indexer"] == str(b)
    assert s["attempts"] == [{"indexer": str(a), "error": s["attempts"][0]["error"], "plugin_mismatch": True}]


def test_unsupported_kotlin_version_hint(tmp_path, kotlin22, monkeypatch):
    bf = kotlin22 / "build.gradle.kts"
    bf.write_text(bf.read_text().replace("2.2.10", "2.4.20"))
    a = _fake(tmp_path / "scip-java", False, MISMATCH)
    b = _fake(tmp_path / "scip-java-0.13", True, MISMATCH)
    monkeypatch.setenv("CODEGRAPH_SCIP_JAVA", f"{a}{os.pathsep}{b}")
    st = index_project(kotlin22, tmp_path / "g.db", "k24")
    s = st["plugins"]["kotlin"]["scip"]
    assert st["plugins"]["kotlin"]["mode"] == "heuristic" and len(s["attempts"]) == 2
    assert "AnalyzerRegistrar is incompatible" in s["status"]
    assert "2.4.20: no released scip-java supports it yet" in s["status"]
    assert _cov(st, "java")["status"] == "unsupported"


# --- Android modules: reported as skipped (scip-java's Gradle plugin compiles no Android variant) ---

def test_android_modules_detected(tmp_path):
    from codegraph.plugins.kotlin.exact import android_modules, skipped_modules
    for mod, text in (("app", 'plugins { alias(libs.plugins.android.application) }'),
                      ("core/data", 'plugins { id("com.android.library") }'),
                      ("server", 'plugins { kotlin("jvm") }')):
        (tmp_path / mod).mkdir(parents=True)
        (tmp_path / mod / "build.gradle.kts").write_text(text)
    assert android_modules(tmp_path) == ["app", "core/data"]
    sk = skipped_modules(["app", "core/data"], ["core/data/src/main/kotlin/A.kt", "server/B.kt"])
    assert [m["module"] for m in sk] == ["app"]


def test_android_module_skipped_on_success(tmp_path, kotlin22, monkeypatch):
    (kotlin22 / "app").mkdir()
    (kotlin22 / "app" / "build.gradle.kts").write_text('plugins { id("com.android.application") }\n')
    tool = _fake(tmp_path / "scip-java-0.13", True, _emit(SCIP22, tmp_path / "runs.log", "x"))
    monkeypatch.setenv("CODEGRAPH_SCIP_JAVA", str(tool))
    st = index_project(kotlin22, tmp_path / "g.db", "k22")
    s = st["plugins"]["kotlin"]["scip"]
    assert st["plugins"]["kotlin"]["mode"] == "scip" and s["android_modules"] == ["app"]
    assert s["skipped_modules"][0]["module"] == "app"
    reason = _cov(st, "kotlin")["reason"]
    assert "skipped modules: app (Android module" in reason


def test_repositories_mode_failure_reason(tmp_path, kotlin22, monkeypatch):
    (kotlin22 / "app").mkdir()
    (kotlin22 / "app" / "build.gradle.kts").write_text('plugins { id("com.android.application") }\n')
    tool = _fake(tmp_path / "scip-java", False,
                 "echo \"Build was configured to prefer settings repositories over project repositories but "
                 "repository 'MavenRepo' was added by plugin class 'SemanticdbGradlePlugin'\"\n"
                 "echo 'BUILD FAILED in 3s'\nexit 1\n")
    monkeypatch.setenv("CODEGRAPH_SCIP_JAVA", str(tool))
    st = index_project(kotlin22, tmp_path / "g.db", "k22")
    status = st["plugins"]["kotlin"]["scip"]["status"]
    assert "prefer settings repositories" in status and "FAIL_ON_PROJECT_REPOS" in status
    assert "Android modules (app) need the Android SDK" in status


def test_scip_java_013_explicit_getter_matches_property(tmp_path, isolated, monkeypatch):
    """scip-java 0.13 defines an explicit `get()` accessor as `C#getX().` at the keyword: it is the property."""
    import shutil
    from codegraph.plugins.native import scipread
    src = tmp_path / "src"
    shutil.copytree(FIX, src)
    shapes = src / "src/main/kotlin/demo/Shapes.kt"
    text = shapes.read_text()
    shapes.write_text(text + "\nclass Holder {\n    val isEmpty: Boolean\n        get() = true\n}\n")
    line = text.count("\n") + 3                            # 0-based line of `get()`
    real = scipread.load

    def load(path):
        idx = real(path)
        o = scipread.Occ(line, 8, 11, "semanticdb maven . . demo/Holder#getIsEmpty().", scipread.DEFINITION, ())
        idx.docs["src/main/kotlin/demo/Shapes.kt"].occs.append(o)
        idx.defs.setdefault(o.symbol, []).append(("src/main/kotlin/demo/Shapes.kt", o))
        return idx
    monkeypatch.setattr(scipread, "load", load)
    st = index_project(src, tmp_path / "g.db", "kotlin-mixed", scip=[str(SCIP22)])
    k = st["plugins"]["kotlin"]
    assert k["mode"] == "scip" and k["scip_defs_unmatched"] == 0 and "scip_defs_unmatched_samples" not in k
