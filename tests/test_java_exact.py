"""Java exact mode (scip-java) and the shared runner with Kotlin.

tests/java_exact_fixture/index.scip was produced by scip-java 0.13.1 (JDK 21) from that Maven project
before Later.java was added, so Later.java stays heuristic.
tests/kotlin_java_mixed_fixture/index.scip is synthetic; regenerate it with
tests/gen_kotlin_java_mixed_scip.py (the comment there says how).
"""
import os
import sqlite3
import stat
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
pytest.importorskip("tree_sitter_java")
from cg_code_graph.indexer import index_project  # noqa: E402

FIX = ROOT / "tests" / "java_exact_fixture"
SCIP = FIX / "index.scip"
MIX = ROOT / "tests" / "kotlin_java_mixed_fixture"


def _index(tmp_path, root, name, **kw):
    db = tmp_path / "g.db"
    st = index_project(root, db, name, **kw)
    return st, sqlite3.connect(db)


def _edges(con, kind="CALLS"):
    return {(s, d): c for s, d, c in con.execute("select src, dst, confidence from edges where kind=?", (kind,))}


def _java(st):
    return next(e for e in st["coverage"]["languages"] if e["language"] == "java")


@pytest.fixture
def quiet(tmp_path, monkeypatch):
    monkeypatch.delenv("CG_JAVA_SCIP", raising=False)
    monkeypatch.delenv("CG_JAVA_SCIP_FILE", raising=False)
    monkeypatch.delenv("CG_KOTLIN_SCIP", raising=False)
    monkeypatch.delenv("CG_KOTLIN_SCIP_FILE", raising=False)
    monkeypatch.setenv("CG_CACHE", str(tmp_path / "cache"))
    monkeypatch.delenv("CG_NO_CACHE", raising=False)


def test_exact_mode_with_scip_flag(tmp_path, quiet):
    st, con = _index(tmp_path, FIX, "java-exact", scip=[str(SCIP)])
    j = st["plugins"]["java"]
    assert j["mode"] == "scip" and j["scip"]["source"] == "--scip"
    assert st["plugins"][f"scip:{SCIP}"]["status"].startswith("imported by the Java plugin")
    calls = _edges(con)
    assert calls[("method:demo.Use.go", "method:demo.Calc.add")] == "exact"
    assert calls[("method:demo.Calc.Inner.bump", "method:demo.Calc.add")] == "exact"
    assert calls[("method:demo.Use.go", "constructor:demo.Calc.<init>")] == "exact"
    # the file the index does not contain keeps heuristic calls
    assert calls[("method:demo.Later.go", "method:demo.Use.go")] == "heuristic"
    inst = _edges(con, "INSTANTIATES")
    assert inst[("method:demo.Use.go", "class:demo.Calc")] == "exact"
    refs = _edges(con, "REFERENCES")
    assert refs[("constructor:demo.Calc.<init>", "field:demo.Calc.value")] == "exact"
    # overloads share one method id
    assert sum(1 for i in con.execute("select id from nodes") if i[0] == "method:demo.Calc.add") == 1
    assert j["scip_defs_unmatched"] == 0 and j["scip_files"] == 3 and j["files"] == 4
    evh = j["exact_vs_heuristic"]
    assert evh["precision"] == 1.0 and evh["agree"] == evh["heuristic_edges"]
    cov = _java(st)
    assert cov["status"] == "exact"
    assert "1 of 4 Java files not in the index keep heuristic calls" in cov["reason"]


def test_java_scip_file_wins_over_kotlin_file(tmp_path, quiet, monkeypatch):
    monkeypatch.setenv("CG_JAVA_SCIP_FILE", str(SCIP))
    monkeypatch.setenv("CG_KOTLIN_SCIP_FILE", str(tmp_path / "other.scip"))
    st, _ = _index(tmp_path, FIX, "java-exact")
    assert st["plugins"]["java"]["scip"]["source"] == "CG_JAVA_SCIP_FILE"
    assert st["plugins"]["java"]["mode"] == "scip"


def test_kotlin_scip_file_still_serves_java(tmp_path, quiet, monkeypatch):
    monkeypatch.setenv("CG_KOTLIN_SCIP_FILE", str(SCIP))
    st, con = _index(tmp_path, FIX, "java-exact")
    assert st["plugins"]["java"]["mode"] == "scip"
    assert st["plugins"]["java"]["scip"]["source"] == "CG_KOTLIN_SCIP_FILE"
    assert _edges(con)[("method:demo.Use.go", "method:demo.Calc.add")] == "exact"


def test_jdk_too_old_note(tmp_path, quiet, monkeypatch):
    from cg_code_graph.plugins.kotlin.exact import java_major
    empty = tmp_path / "bin"
    empty.mkdir()
    java = empty / "java"
    java.write_text('#!/bin/sh\nif [ "$1" = "-version" ]; then echo \'openjdk version "11.0.2"\' >&2; exit 0; fi\nexit 0\n')
    java.chmod(java.stat().st_mode | stat.S_IEXEC)
    tool = empty / "scip-java"
    tool.write_text("#!/bin/sh\nexit 0\n")
    tool.chmod(tool.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", str(empty))
    monkeypatch.delenv("JAVA_HOME", raising=False)
    monkeypatch.setenv("CG_SCIP_JAVA", str(tool))
    monkeypatch.setenv("CG_JAVA_SCIP", "1")
    assert java_major(str(java)) == 11
    st, _ = _index(tmp_path, FIX, "java-exact")
    status = st["plugins"]["java"]["scip"]["status"]
    assert "JDK too old" in status and "unsupported Java 8/11 build JDK" in status
    assert st["plugins"]["java"]["mode"] == "heuristic"
    from cg_code_graph import doctor
    row = next(x for x in doctor.report(FIX)["languages"] if x["language"] == "java")
    assert row["mode"] == "heuristic" and "JDK too old" in row["why"] and "found 11" in row["why"]


def test_heuristic_kotlin_java_edges(tmp_path, quiet, monkeypatch):
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    monkeypatch.delenv("JAVA_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    st, con = _index(tmp_path, MIX, "mixed")
    assert st["plugins"]["kotlin"]["mode"] == "heuristic"
    assert st["plugins"]["java"]["mode"] == "heuristic"
    calls = _edges(con)
    assert calls[("method:demo.OrderEvents.onPlaced", "method:demo.OrderService.find")] == "heuristic"
    assert calls[("method:demo.FromJava.a", "function:demo.top")] == "heuristic"
    assert calls[("method:demo.FromJava.b", "method:demo.Widget.Companion.make")] == "heuristic"
    assert calls[("method:demo.FromJava.c", "method:demo.Widget.Companion.stat")] == "heuristic"
    reads = _edges(con, "READS_PROP")
    writes = _edges(con, "WRITES_PROP")
    assert reads[("method:demo.FromJava.d", "field:demo.Widget.name")] == "resolved"
    assert writes[("method:demo.FromJava.e", "field:demo.Widget.count")] == "resolved"
    assert reads[("method:demo.FromJava.f", "field:demo.Widget.ready")] == "resolved"
    ids = [r[0] for r in con.execute("select id from nodes")]
    assert len(ids) == len(set(ids))
    assert not any("::" in i for i in ids)


def test_missing_scip_java_is_a_message(tmp_path, quiet, monkeypatch):
    empty = tmp_path / "bin"
    empty.mkdir()
    java = empty / "java"
    java.write_text("#!/bin/sh\nexit 0\n")
    java.chmod(0o755)
    monkeypatch.setenv("PATH", f"{empty}{os.pathsep}/usr/bin{os.pathsep}/bin")
    monkeypatch.delenv("JAVA_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("CG_SCIP_JAVA", raising=False)
    monkeypatch.setenv("CG_JAVA_SCIP", "1")
    st, _ = _index(tmp_path, FIX, "java-exact")
    status = st["plugins"]["java"]["scip"]["status"]
    assert status.startswith("scip-java not installed")
    assert "CG_JAVA_SCIP=1" in status
    assert st["plugins"]["java"]["mode"] == "heuristic"


def test_cache_key_includes_jdk_and_build_tool(tmp_path):
    from cg_code_graph.plugins.kotlin.exact import scip_cache_extra
    jdk21 = scip_cache_extra("scip-java", "/usr/lib/jvm/21/bin/java", tmp_path, "pom.xml")
    jdk17 = scip_cache_extra("scip-java", "/usr/lib/jvm/17/bin/java", tmp_path, "pom.xml")
    gradle = scip_cache_extra("scip-java", "/usr/lib/jvm/21/bin/java", tmp_path, "build.gradle.kts")
    assert jdk21 != jdk17 and jdk21 != gradle
    assert "jdk=/usr/lib/jvm/21/bin/java" in jdk21 and "build=pom.xml" in jdk21


def test_enum_anonymous_and_generic_symbols(tmp_path, quiet):
    """scip-java `$anon`, enum terms, and `id(+1)` land on the Java plugin's ids."""
    sys.path.insert(0, str(ROOT / "tests"))
    from gen_kotlin_java_mixed_scip import write_index
    root = tmp_path / "shapes"
    src = root / "src" / "main" / "java" / "demo"
    src.mkdir(parents=True)
    (root / "pom.xml").write_text("<project/>\n")
    text = """\
package demo;

public class Shapes {
    public int value;

    public Shapes(int value) {
        this.value = value;
    }

    public enum Color { RED, BLUE }

    public int pick(Color c) {
        return c == Color.RED ? 1 : 2;
    }

    public void go() {
        Runnable r = new Runnable() {
            public void run() { pick(Color.BLUE); }
        };
        r.run();
        id(1);
    }

    public <T> T id(T value) { return value; }

    public int id(int value) { return value; }
}
"""
    (src / "Shapes.java").write_text(text)
    lines = text.splitlines()

    def at(token, n=0):
        seen = 0
        for i, line in enumerate(lines):
            if token in line:
                if seen == n:
                    return i
                seen += 1
        raise AssertionError(token)

    rel = "src/main/java/demo/Shapes.java"
    scheme = "scip-java maven maven/demo/shapes 1.0 "
    occs = [
        (at("class Shapes"), scheme + "demo/Shapes#", True),
        (at("int value"), scheme + "demo/Shapes#value.", True),
        (at("this.value"), scheme + "demo/Shapes#value.", False),
        (at("Shapes("), scheme + "demo/Shapes#`<init>`().", True),
        (at("enum Color"), scheme + "demo/Shapes#Color#", True),
        (at("RED"), scheme + "demo/Shapes#Color#RED.", True),
        (at("Color.RED"), scheme + "demo/Shapes#Color#RED.", False),
        (at("int pick"), scheme + "demo/Shapes#pick().", True),
        (at("void go"), scheme + "demo/Shapes#go().", True),
        (at("new Runnable"), scheme + "demo/Shapes#go().`$anon`#", True),
        (at("void run"), scheme + "demo/Shapes#go().`$anon`#run().", True),
        (at("pick(Color.BLUE)"), scheme + "demo/Shapes#pick().", False),
        (at("Color.BLUE"), scheme + "demo/Shapes#Color#BLUE.", False),
        (at("<T>"), scheme + "demo/Shapes#id().", True),
        (at("int id"), scheme + "demo/Shapes#id(+1).", True),
        (at("id(1)"), scheme + "demo/Shapes#id(+1).", False),
        (at("<T>"), scheme + "demo/Shapes#id().[T]", True),
    ]
    scip = root / "index.scip"
    write_index(scip, [(rel, "java", occs)], version="0.13-test")
    st, con = _index(tmp_path, root, "shapes", scip=[str(scip)])
    j = st["plugins"]["java"]
    assert j["mode"] == "scip" and j["scip_defs_unmatched"] == 0
    calls = _edges(con)
    assert calls[("method:demo.Shapes.go.1.run", "method:demo.Shapes.pick")] == "exact"
    assert calls[("method:demo.Shapes.go", "method:demo.Shapes.id")] == "exact"
    refs = _edges(con, "REFERENCES")
    assert refs[("constructor:demo.Shapes.<init>", "field:demo.Shapes.value")] == "exact"
    assert refs[("method:demo.Shapes.pick", "enum_case:demo.Shapes.Color.RED")] == "exact"
    ids = [r[0] for r in con.execute("select id from nodes where id like '%Shapes%'")]
    assert ids.count("method:demo.Shapes.id") == 1
    assert "class:demo.Shapes.Color" in ids and "class:demo.Shapes.go.1" in ids
    assert not any("::" in i or "$anon" in i for i in ids)


def test_doctor_java_row_names_the_opt_in(tmp_path, monkeypatch):
    from cg_code_graph import doctor
    empty = tmp_path / "bin"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    monkeypatch.delenv("JAVA_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("CG_SCIP_JAVA", raising=False)
    monkeypatch.delenv("CG_JAVA_SCIP", raising=False)
    monkeypatch.delenv("CG_KOTLIN_SCIP", raising=False)
    monkeypatch.delenv("CG_JAVA_SCIP_FILE", raising=False)
    monkeypatch.delenv("CG_KOTLIN_SCIP_FILE", raising=False)
    row = next(x for x in doctor.report(FIX)["languages"] if x["language"] == "java")
    assert row["mode"] == "heuristic"
    assert "install.sh --with java" in row["fix"] and "CG_JAVA_SCIP=1" in row["fix"]


def test_one_scip_java_run_serves_both_plugins(tmp_path, quiet, monkeypatch):
    """CG_JAVA_SCIP=1 alone starts the run; Kotlin reads the same cached index."""
    log = tmp_path / "runs.log"
    tool = tmp_path / "scip-java"
    tool.write_text(
        "#!/bin/sh\n"
        '[ "$1" = version ] && { echo 0.13-test; exit 0; }\n'
        '[ "$1" = --help ] && { echo aggregate; exit 0; }\n'
        f'echo run >> "{log}"\n'
        'for a in "$@"; do case "$a" in --output=*) cp "' + str(MIX / "index.scip") + '" "${a#--output=}";; esac; done\n'
        "exit 0\n")
    tool.chmod(0o755)
    java = tmp_path / "java"
    java.write_text("#!/bin/sh\nexit 0\n")
    java.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}/usr/bin{os.pathsep}/bin")
    monkeypatch.setenv("CG_SCIP_JAVA", str(tool))
    monkeypatch.setenv("CG_JAVA_SCIP", "1")
    monkeypatch.delenv("JAVA_HOME", raising=False)
    st, con = _index(tmp_path, MIX, "mixed")
    assert st["plugins"]["java"]["mode"] == "scip"
    assert st["plugins"]["kotlin"]["mode"] == "scip"
    assert st["plugins"]["java"]["scip"]["source"] == "scip-java"
    assert st["plugins"]["kotlin"]["scip"]["cache"] == "hit" or st["plugins"]["java"]["scip"]["cache"] == "miss"
    assert log.read_text().count("run") == 1
    calls = _edges(con)
    assert calls[("method:demo.OrderEvents.onPlaced", "method:demo.OrderService.find")] == "exact"
    assert calls[("method:demo.FromJava.a", "function:demo.top")] == "exact"
    assert calls[("method:demo.FromJava.b", "method:demo.Widget.Companion.make")] == "exact"
    assert calls[("method:demo.FromJava.c", "method:demo.Widget.Companion.stat")] == "exact"
    reads = _edges(con, "READS_PROP")
    writes = _edges(con, "WRITES_PROP")
    assert reads[("method:demo.FromJava.d", "field:demo.Widget.name")] == "exact"
    assert writes[("method:demo.FromJava.e", "field:demo.Widget.count")] == "exact"
    assert reads[("method:demo.FromJava.f", "field:demo.Widget.ready")] == "exact"
    assert calls[("method:demo.Missed.go", "method:demo.OrderService.find")] == "heuristic"
    ids = [r[0] for r in con.execute("select id from nodes")]
    assert len(ids) == len(set(ids))
    assert not any("::" in i or "$anon" in i for i in ids)
    cov = _java(st)
    assert "1 of 3 Java files not in the index keep heuristic calls" in cov["reason"]
