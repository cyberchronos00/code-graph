"""Per-platform C source lists from CMake (#111): a file the CMake lists add only under platform conditions is built
only there, which replaces the directory / file-name guess; a Linux-named file without such a list is unknown on
Android rather than missing there."""
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from cg_code_graph.cmake_sources import membership  # noqa: E402
from cg_code_graph.indexer import index_project  # noqa: E402

CMAKE = """\
cmake_minimum_required(VERSION 3.10)
project(ticker C)
set(ticker_sources src/ticker.c)   # everywhere
if(WIN32)
  list(APPEND ticker_sources src/win/clock.c)
else()
  list(APPEND ticker_sources src/unix/clock.c)
  if(NOT CMAKE_SYSTEM_NAME MATCHES "Android|Linux")
    list(APPEND ticker_sources src/unix/fallback.c)
  endif()
endif()
if(CMAKE_SYSTEM_NAME STREQUAL "Android")
  list(APPEND ticker_sources src/unix/linux.c)
elseif(CMAKE_SYSTEM_NAME STREQUAL "Linux")
  list(APPEND ticker_sources ${CMAKE_CURRENT_SOURCE_DIR}/src/unix/linux.c)
endif()
if(APPLE OR CMAKE_SYSTEM_NAME MATCHES "FreeBSD")
  list(APPEND ticker_sources src/unix/kq.c)
endif()
if(TICKER_EXTRAS)
  list(APPEND ticker_sources src/extras.c)
endif()
function(add_tool name)
  add_executable(${name} src/tool.c)
endfunction()
add_library(ticker ${ticker_sources})
"""

FILES = {
    "CMakeLists.txt": CMAKE,
    "src/ticker.c": "int now_ticks(void);\nint ticker_value(void) { return now_ticks(); }\n",
    "src/win/clock.c": "int now_ticks(void) { return 1; }\n",
    "src/unix/clock.c": "int linux_ticks(void);\nint now_ticks(void) {\n#ifdef __linux__\n  return linux_ticks();\n#else\n  return 2;\n#endif\n}\n",
    "src/unix/linux.c": "int linux_ticks(void) { return 3; }\n",
    "src/unix/fallback.c": "int fallback_ticks(void) { return 4; }\n",
    "src/unix/kq.c": "int kq_ticks(void) {\n#ifdef __APPLE__\n  return 5;\n#else\n  return kq_other();\n#endif\n}\nint kq_other(void) { return 6; }\n",
    "src/extras.c": "int extra_ticks(void) { return 7; }\n",
    "src/tool.c": "int main(void) { return 0; }\n",
}


def _write(root, files):
    for rel, body in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body)


def _on(m, f):
    return [p for p, v in m[f]["values"].items() if v is True]


def test_membership_reads_platform_conditions(tmp_path):
    _write(tmp_path, FILES)
    m = membership(tmp_path, set(FILES) - {"CMakeLists.txt"})
    assert "src/ticker.c" not in m                    # listed unconditionally
    assert "src/extras.c" not in m                    # an option: unknown everywhere, nothing to narrow
    assert "src/tool.c" not in m                      # inside a function(): built where it is called
    assert _on(m, "src/win/clock.c") == ["windows"]
    assert "windows" not in _on(m, "src/unix/clock.c") and "linux" in _on(m, "src/unix/clock.c")
    assert _on(m, "src/unix/linux.c") == ["linux", "android"]
    assert _on(m, "src/unix/fallback.c") == ["macos", "ios", "web", "tvos", "watchos", "visionos"]
    assert m["src/unix/linux.c"]["mentions"] == 2 and not m["src/unix/linux.c"]["elsewhere"]
    assert m["src/unix/kq.c"]["elsewhere"]           # FreeBSD builds it too


def test_cmake_lists_replace_the_path_guess(tmp_path):
    _write(tmp_path, FILES)
    st = index_project(tmp_path, tmp_path / "t.db", "ticker")
    db = sqlite3.connect(tmp_path / "t.db")
    a = json.loads(db.execute("SELECT attrs FROM nodes WHERE id = 'function:linux_ticks'").fetchone()[0])
    assert a["platforms"] == ["linux"] and a.get("platforms_other") == ["android"]
    assert a["platform_expr"].startswith('CMake if(CMAKE_SYSTEM_NAME STREQUAL "Android")')
    d = st["platforms"]["divergence"]
    # src/unix/clock.c calls linux_ticks under #ifdef __linux__: built on Linux and Android, as the list says
    assert not [x for x in d["missing_callee"] if x["to"] == "function:linux_ticks"]
    # kq.c is also built on FreeBSD, where its #else is live: not a dead branch
    assert not d["dead_branch"]
    e = {r for r in db.execute("SELECT src, dst FROM edges WHERE kind = 'CALLS'")}
    assert ("function:kq_ticks", "function:kq_other") in e


def test_linux_named_file_without_a_build_list_is_unknown_on_android(tmp_path):
    _write(tmp_path, {"src/unix/core.c": "int sys_ticks(void);\nint core_ticks(void) { return sys_ticks(); }\n",
                      "src/unix/linux.c": "int sys_ticks(void) { return 1; }\n"})
    st = index_project(tmp_path, tmp_path / "u.db", "u")
    db = sqlite3.connect(tmp_path / "u.db")
    a = json.loads(db.execute("SELECT attrs FROM nodes WHERE id = 'function:sys_ticks'").fetchone()[0])
    assert "android" in (a.get("platform_unknown") or []) + (a.get("platforms_other") or [])
    assert not [x for x in st["platforms"]["divergence"]["missing_callee"] if "android" in x["missing_on"]]


def test_one_compile_database_per_target(tmp_path):
    _write(tmp_path, FILES)
    root = tmp_path.resolve()
    lin = [{"directory": str(root), "file": "src/ticker.c"}, {"directory": str(root), "file": "src/unix/clock.c"},
           {"directory": str(root / "src"), "file": "unix/linux.c"}]
    win = [{"directory": str(root), "file": str(root / "src/ticker.c")}, {"directory": str(root), "file": "src/win/clock.c"}]
    _write(tmp_path, {"build-linux/compile_commands.json": json.dumps(lin), "build-win/compile_commands.json": json.dumps(win),
                      ".cg.yaml": "platforms:\n  compile_commands:\n    linux: build-linux/compile_commands.json\n"
                                  "    win32: build-win/compile_commands.json\n"})
    index_project(tmp_path, tmp_path / "c.db", "ticker")
    db = sqlite3.connect(tmp_path / "c.db")

    def attrs(nid):
        r = db.execute("SELECT attrs FROM nodes WHERE id = ?", (nid,)).fetchone()
        return json.loads(r[0] or "{}")
    a = attrs("function:linux_ticks")
    # not compiled by the Windows build; no database for macOS: unknown there, kept live
    assert a["platforms"] == ["linux", "macos"] and a["platform_unknown"] == ["macos"]
    assert a["platform_expr"] == "compile_commands (linux)"
    assert attrs("function:src/win/clock.c#now_ticks")["platforms"][:1] == ["windows"]
    assert "platforms" not in attrs("function:ticker_value")        # compiled by every listed target
    # a source no listed build compiles is built on none of them (the CMake list would have said FreeBSD / Apple)
    assert attrs("function:kq_ticks")["platform_expr"] == "compile_commands (no listed target)"
