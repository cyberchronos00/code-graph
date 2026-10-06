"""Every module compiles on the oldest supported Python (pyproject `requires-python`, 3.11).

`cg index` imports every language plugin, so a single module using syntax newer than the interpreter (a backslash or
a same-quote string inside an f-string expression, PEP 701, or type parameter lists, PEP 695) makes indexing fail
for every language on that Python. The checks here run on any interpreter: the running one compiles each module,
`ast.parse(feature_version=(3, 11))` rejects the version-gated grammar, a tokenizer pass flags the PEP 701 f-string
forms (which `feature_version` does not gate), and a Python 3.11 found on the machine byte-compiles the package.
`cg doctor` imports every module too and exits non-zero when one fails."""
import ast
import io
import json
import shutil
import subprocess
import sys
import tokenize
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PKG = ROOT / "cg_code_graph"
SKIP_PARTS = {"node_modules", "vendor", ".dart_tool", "__pycache__", ".bin"}
MIN = (3, 11)


def _modules():
    return sorted(p for p in PKG.rglob("*.py") if not SKIP_PARTS & set(p.relative_to(PKG).parts))


def _quote(tok: str) -> str:
    body = tok.lstrip("rRbBfFuU")
    return body[:3] if body[:3] in ('"""', "'''") else body[:1]


def pep701_fstring_uses(src: str) -> list[str]:
    """f-string expression parts Python 3.11 rejects: a backslash, a comment, a line break in a single-quoted
    f-string, or a string using the enclosing f-string's quote. Needs the 3.12+ tokenizer (FSTRING_* tokens)."""
    out, stack = [], []          # stack: one entry per open f-string: [quote, braces open in its expression part]
    for t in tokenize.generate_tokens(io.StringIO(src).readline):
        exprs = [e for e in stack if e[1] > 0]       # f-strings whose expression part encloses this token
        line = t.start[0]
        if t.type == tokenize.FSTRING_START:
            q = _quote(t.string)
            if any(e[0] == q or (len(e[0]) == 1 and q[0] == e[0]) for e in exprs):
                out.append(f"line {line}: nested f-string reuses the enclosing quote")
            stack.append([q, 0])
        elif t.type == tokenize.FSTRING_END:
            stack.pop()
        elif t.type == tokenize.FSTRING_MIDDLE:
            if "\\" in t.string and any(e[1] > 0 for e in stack[:-1]):
                out.append(f"line {line}: backslash inside an f-string expression")
        elif stack and t.type == tokenize.OP and t.string in "{}":
            stack[-1][1] += 1 if t.string == "{" else -1
        elif exprs and t.type == tokenize.STRING:
            q = _quote(t.string)
            if "\\" in t.string:
                out.append(f"line {line}: backslash inside an f-string expression")
            if any(e[0] == q or (len(e[0]) == 1 and q[0] == e[0]) for e in exprs):
                out.append(f"line {line}: string inside an f-string expression reuses the enclosing quote")
        elif exprs and t.type == tokenize.COMMENT:
            out.append(f"line {line}: comment inside an f-string expression")
        elif exprs and t.type in (tokenize.NL, tokenize.NEWLINE) and any(len(e[0]) == 1 for e in exprs):
            out.append(f"line {line}: line break inside a single-quoted f-string expression")
    return out


def test_requires_python_is_the_floor_checked_here():
    meta = tomllib.loads((ROOT / "pyproject.toml").read_text())
    assert meta["project"]["requires-python"] == ">=%d.%d" % MIN


def test_every_module_compiles_for_the_oldest_supported_python():
    mods = _modules()
    assert len(mods) > 100 and PKG / "plugins" / "kotlin" / "plugin.py" in mods
    bad = []
    for p in mods:
        src = p.read_text(encoding="utf-8")
        rel = p.relative_to(ROOT).as_posix()
        try:
            compile(src, rel, "exec", dont_inherit=True)
            ast.parse(src, rel, feature_version=MIN)
        except SyntaxError as e:
            bad.append(f"{rel}:{e.lineno}: {e.msg}")
            continue
        if sys.version_info >= (3, 12):
            bad += [f"{rel} {x}" for x in pep701_fstring_uses(src)]
    assert not bad, "syntax newer than Python %d.%d:\n" % MIN + "\n".join(bad)


@pytest.mark.skipif(sys.version_info < (3, 12), reason="FSTRING_* tokens exist from Python 3.12")
def test_pep701_checker_flags_what_311_rejects():
    flagged = lambda s: pep701_fstring_uses(s + "\n")
    assert flagged("""x = f"route:{re.sub(r'{(\\w+)}', r'{\\1}', uri)}\"""")          # the 0.7.0 Kotlin plugin line
    assert flagged("""x = f"{d["k"]}\"""")
    assert flagged("""x = f"{f"{y}"}\"""")
    assert flagged("x = f'{a # note\n}'")
    assert flagged("x = f'{a +\n b}'")
    for ok in ("""x = f"{d['k']}\"""", "x = f'{a}\\n{b!r:>{w}}'", 'x = f"""{d["k"]}"""', "x = f'{{literal}}'",
               "x = f'{x:%Y-%m}'"):
        assert not flagged(ok), ok


def _python311():
    exe = shutil.which("python3.11")
    if not exe and shutil.which("uv"):
        r = subprocess.run(["uv", "python", "find", "--no-project", "3.11"], capture_output=True, text=True)
        exe = r.stdout.strip() if r.returncode == 0 else None
    return exe


def test_package_byte_compiles_under_python_311():
    exe = sys.executable if sys.version_info[:2] == MIN else _python311()
    if not exe:
        pytest.skip("no Python 3.11 on this machine (`uv python install 3.11`)")
    code = ("import sys, pathlib\nbad = []\nskip = set(sys.argv[2:])\n"
            "for p in sorted(pathlib.Path(sys.argv[1]).rglob('*.py')):\n"
            "    if skip & set(p.parts): continue\n"
            "    try: compile(p.read_text(encoding='utf-8'), str(p), 'exec', dont_inherit=True)\n"
            "    except SyntaxError as e: bad.append(f'{p}:{e.lineno}: {e.msg}')\n"
            "print('\\n'.join(bad)); sys.exit(1 if bad else 0)\n")
    r = subprocess.run([exe, "-c", code, str(PKG), *SKIP_PARTS], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr


def test_doctor_imports_every_module():
    from cg_code_graph.doctor import module_imports
    r = module_imports()
    assert r["modules"] > 100 and r["failed"] == {}


def test_doctor_reports_a_module_that_does_not_import(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CG_CACHE", str(tmp_path / "cache"))
    from cg_code_graph import cli, doctor
    try:
        compile("x = (\n", "cg_code_graph/plugins/kotlin/plugin.py", "exec")
    except SyntaxError as e:
        err = doctor._describe(e)
    assert err.startswith("SyntaxError: ") and err.endswith("(cg_code_graph/plugins/kotlin/plugin.py:1)")
    fake = {"modules": 111, "failed": {"cg_code_graph.indexer": err, "cg_code_graph.plugins.kotlin.plugin": err}}
    monkeypatch.setattr(doctor, "module_imports", lambda: fake)
    (tmp_path / "a.py").write_text("print(1)\n")
    (tmp_path / "Main.kt").write_text("fun main() {}\n")
    r = doctor.report(tmp_path)
    langs = {x["language"]: x for x in r["languages"]}
    assert langs["kotlin"]["mode"] == "broken" and "cg_code_graph.plugins.kotlin.plugin does not import" in langs["kotlin"]["why"]
    assert langs["python"]["mode"] == "broken" and "cg index cannot load" in langs["python"]["why"]
    txt = doctor.render(r)
    assert "cg modules: 2 of 111 do not import" in txt and "plugins/kotlin/plugin.py:1" in txt
    assert cli.main(["doctor", str(tmp_path)]) == 1
    capsys.readouterr()
    assert cli.main(["doctor", str(tmp_path), "--json"]) == 1
    assert json.loads(capsys.readouterr().out)["modules"]["failed"]
