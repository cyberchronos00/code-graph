"""Enum cases and constants in Kotlin, TypeScript, Python and PHP (#84; Swift: test_swift_values.py): the nodes
under their type or module, USES_VALUE edges where the binding is certain (a local of the same name shadows), and no
CALLS edge to them. Rust (`variant`, `const`, `static`) and C / C++ (`enumerator`, `global`) keep their own kinds."""
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from cg_code_graph.indexer import index_project  # noqa: E402
from sample import EXTRACTOR_DEPS  # noqa: E402

FIXTURES = {
 "kotlin": {
  "V.kt": "package a.b\n\nimport a.b.Color.RED\n\nconst val MAX = 3\nval top = listOf(1)\nvar counter = 0\n\nenum class Color(val v: Int) {\n    RED(1),\n    GREEN(2) {\n        override fun label() = \"g\"\n    };\n\n    open fun label() = \"c\"\n}\n\nclass K {\n    companion object {\n        const val A = 1\n        val B = 2\n        val c get() = 3\n        fun make() = K()\n    }\n    fun own() = A\n}\n\nobject O {\n    val Z = 1\n    private var hidden = 2\n}\n\nfun f(c: Color): Int {\n    val x = Color.RED\n    val y = when (c) {\n        Color.GREEN -> 1\n        RED -> 2\n    }\n    val MAX = 7\n    return K.A + O.Z + K.B + y + MAX + top.size + x.v + K.make().hashCode()\n}\n\nfun g() = MAX\n"
 },
 "ts": {
  "package.json": "{\"name\":\"v\",\"version\":\"1.0.0\"}\n",
  "tsconfig.json": "{\"compilerOptions\":{\"strict\":true,\"target\":\"es2020\",\"module\":\"esnext\",\"moduleResolution\":\"node\"},\"include\":[\"src\"]}\n",
  "src/values.ts": "export enum Color { Red, Green = 'g' }\nexport const MAX = 3\nexport const NAMES = ['a', 'b'] as const\nexport const handlers = { run: () => 1 }\nexport const app = makeApp()\nfunction makeApp() { return {} }\nexport class Cfg {\n  static readonly LIMIT = 10\n  static counter = 0\n  limit() { return Cfg.LIMIT + MAX }\n}\n",
  "src/use.ts": "import { Color, MAX as M } from './values'\nimport * as v from './values'\nexport function pick(c: Color): number {\n  if (c === Color.Red) return M\n  const MAX = 9\n  return v.MAX + MAX + v.Cfg.LIMIT + v.NAMES.length\n}\n"
 },
 "python": {
  "pkg/__init__.py": "",
  "pkg/values.py": "import enum\nfrom typing import Final, TypeVar\n\nT = TypeVar(\"T\")\nMAX_RETRIES = 3\ntimeout: Final = 5\nlower = 1\n\n\nclass Color(enum.Enum):\n    RED = 1\n    GREEN = enum.auto()\n    _ignore_ = [\"x\"]\n\n    def label(self):\n        return \"r\" if self is Color.RED else \"g\"\n\n\nclass Sub(str, enum.Enum):\n    A = \"a\"\n\n\ndef limit():\n    return MAX_RETRIES + timeout\n",
  "pkg/use.py": "from pkg import values\nfrom pkg.values import Color, MAX_RETRIES as M\n\nDEFAULT = Color.GREEN\n\n\ndef run(c: Color):\n    if c == Color.RED:\n        return M + values.MAX_RETRIES\n    MAX_RETRIES = 9\n    return MAX_RETRIES + values.Sub.A.value\n\n\ndef shadow(M):\n    return M\n"
 },
 "php": {
  "composer.json": "{\"name\":\"v/v\",\"autoload\":{\"psr-4\":{\"App\\\\\":\"src/\"}}}\n",
  "src/Status.php": "<?php\nnamespace App;\n\ninterface HasLimit { const LIMIT = 10; }\n\nenum Status: string {\n    case Active = 'a';\n    case Closed = 'c';\n    const DEFAULT = self::Active;\n\n    public function label(): string { return $this === self::Active ? 'on' : 'off'; }\n}\n\nclass Base implements HasLimit { public const MAX = 3; }\n\nclass Svc extends Base {\n    public function run(): int {\n        $s = Status::Closed;\n        return self::MAX + static::LIMIT + Base::MAX + strlen(Status::class);\n    }\n}\n"
 }
}


def build(tmp_path, lang):
    for rel, body in FIXTURES[lang].items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body)
    d = Path(tempfile.mkdtemp())
    index_project(tmp_path, d / "g.db", lang)
    db = sqlite3.connect(d / "g.db")
    nodes = {i for (i,) in db.execute("SELECT id FROM nodes WHERE kind IN ('enum_case', 'constant')")}
    uses = {(s, t, ln) for s, t, ln in db.execute("SELECT src, dst, line FROM edges WHERE kind='USES_VALUE'")}
    calls = list(db.execute("SELECT dst FROM edges WHERE kind IN ('CALLS','TEST_CALLS') AND (dst LIKE 'enum_case:%' "
                            "OR dst LIKE 'constant:%')"))
    assert not calls
    return nodes, uses


def test_kotlin(tmp_path):
    pytest.importorskip("tree_sitter_kotlin")
    nodes, uses = build(tmp_path, "kotlin")
    assert nodes == {"constant:a.b.MAX", "constant:a.b.top", "enum_case:a.b.Color.RED", "enum_case:a.b.Color.GREEN",
                     "constant:a.b.K.A", "constant:a.b.K.B", "constant:a.b.O.Z", "constant:a.b.O.hidden"}
    assert uses == {
        ("method:a.b.K.own", "constant:a.b.K.A", 25),               # a companion constant, bare in its class
        ("function:a.b.f", "enum_case:a.b.Color.RED", 34),
        ("function:a.b.f", "enum_case:a.b.Color.GREEN", 36),        # a `when` branch
        ("function:a.b.f", "enum_case:a.b.Color.RED", 37),          # imported entry
        ("function:a.b.f", "constant:a.b.K.A", 40),
        ("function:a.b.f", "constant:a.b.O.Z", 40),
        ("function:a.b.f", "constant:a.b.K.B", 40),
        ("function:a.b.f", "constant:a.b.top", 40),                 # not `MAX`: the local `val MAX = 7`
        ("function:a.b.g", "constant:a.b.MAX", 43),
    }


@pytest.mark.skipif(not EXTRACTOR_DEPS.exists(), reason="run `npm ci` in cg_code_graph/plugins/ts/extractor")
def test_typescript(tmp_path):
    nodes, uses = build(tmp_path, "ts")
    assert nodes == {"enum_case:src/values.ts#Color.Red", "enum_case:src/values.ts#Color.Green",
                     "constant:src/values.ts#MAX", "constant:src/values.ts#NAMES",
                     "constant:src/values.ts#Cfg.LIMIT"}               # not `handlers` (functions), `app` (a call)
    assert uses == {
        ("function:src/use.ts#pick", "enum_case:src/values.ts#Color.Red", 4),
        ("function:src/use.ts#pick", "constant:src/values.ts#MAX", 4),      # `MAX as M`
        ("function:src/use.ts#pick", "constant:src/values.ts#MAX", 6),      # `v.MAX`, not the local `MAX`
        ("function:src/use.ts#pick", "constant:src/values.ts#Cfg.LIMIT", 6),
        ("function:src/use.ts#pick", "constant:src/values.ts#NAMES", 6),
        ("method:src/values.ts#Cfg.limit", "constant:src/values.ts#Cfg.LIMIT", 10),
        ("method:src/values.ts#Cfg.limit", "constant:src/values.ts#MAX", 10),
    }


def test_python(tmp_path):
    nodes, uses = build(tmp_path, "python")
    assert nodes == {"constant:pkg.use.DEFAULT", "constant:pkg.values.MAX_RETRIES", "constant:pkg.values.timeout",
                     "enum_case:pkg.values.Color.RED", "enum_case:pkg.values.Color.GREEN",
                     "enum_case:pkg.values.Sub.A"}                     # not `T = TypeVar(...)`, `lower`, `_ignore_`
    assert uses == {
        ("module:pkg.use", "enum_case:pkg.values.Color.GREEN", 4),
        ("function:pkg.use.run", "enum_case:pkg.values.Color.RED", 8),
        ("function:pkg.use.run", "constant:pkg.values.MAX_RETRIES", 9),     # `M` and `values.MAX_RETRIES`
        ("function:pkg.use.run", "enum_case:pkg.values.Sub.A", 11),          # not the local MAX_RETRIES
        ("method:pkg.values.Color.label", "enum_case:pkg.values.Color.RED", 16),
        ("function:pkg.values.limit", "constant:pkg.values.MAX_RETRIES", 24),
        ("function:pkg.values.limit", "constant:pkg.values.timeout", 24),
    }                                                                   # `shadow(M)`: its parameter


@pytest.mark.skipif(not shutil.which("php"), reason="php not installed")
def test_php(tmp_path):
    nodes, uses = build(tmp_path, "php")
    assert nodes == {"constant:App\\HasLimit::LIMIT", "enum_case:App\\Status::Active", "enum_case:App\\Status::Closed",
                     "constant:App\\Status::DEFAULT", "constant:App\\Base::MAX"}
    assert uses == {
        ("method:App\\Status::label", "enum_case:App\\Status::Active", 11),
        ("method:App\\Svc::run", "enum_case:App\\Status::Closed", 18),
        ("method:App\\Svc::run", "constant:App\\Base::MAX", 19),         # `self::MAX`, `Base::MAX`: inherited
        ("method:App\\Svc::run", "constant:App\\HasLimit::LIMIT", 19),   # an interface's, through `static::`
    }
