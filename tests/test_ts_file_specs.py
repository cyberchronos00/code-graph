"""`file#name` query specs for TypeScript / JavaScript symbols (#28). Fixture written from scratch in a temp dir."""
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from codegraph import query as Q  # noqa: E402
from codegraph.core.store import GraphStore  # noqa: E402
from codegraph.indexer import index_project  # noqa: E402
from sample import EXTRACTOR_DEPS  # noqa: E402

pytestmark = pytest.mark.skipif(not EXTRACTOR_DEPS.exists(), reason="run `npm install` in codegraph/plugins/ts/extractor")

FILES = {
    "package.json": '{"name": "orders", "dependencies": {"express": "^4"}}\n',
    "tsconfig.json": '{"compilerOptions": {"allowJs": true, "module": "commonjs"}}\n',
    "src/a/app.ts": "export function listOrders() { return [1] }\nexport function useA() { return listOrders() }\n",
    "src/b/app.ts": "export function listOrders() { return [2] }\nexport function useB() { return listOrders() }\n",
    "src/svc.ts": "export class OrderService {\n  create(x: number) { return x }\n}\n"
                  "export function make() { return new OrderService().create(1) }\n",
    "src/codec.ts": "const codec = (o: object) => o\n"
                    "export const dates = codec({ decode: (s: string) => new Date(s) })\n"
                    "export const nums = codec({ decode: (s: string) => Number(s) })\n",
}
A, B = "function:src/a/app.ts#listOrders", "function:src/b/app.ts#listOrders"
CREATE = "method:src/svc.ts#OrderService.create"


@pytest.fixture(scope="module")
def db(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("ts_specs")
    root = tmp / "orders"
    for rel, body in FILES.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body)
    index_project(root, tmp / "o.db", "orders")
    return tmp / "o.db"


def test_file_name_specs_pick_one_file(db):
    st = GraphStore(db)
    assert Q.resolve_targets(st, "src/a/app.ts#listOrders") == [A]
    assert Q.resolve_targets(st, "a/app.ts#listOrders") == [A]          # path suffix
    assert Q.resolve_targets(st, "./src/b/app.ts#listOrders") == [B]
    assert sorted(Q.resolve_targets(st, "app.ts#listOrders")) == [A, B]  # a suffix both files share
    assert sorted(Q.resolve_targets(st, "listOrders")) == [A, B]         # the bare name still lists both
    assert Q.resolve_targets(st, A) == [A]                               # the full id keeps working
    assert Q.resolve_targets(st, "pp.ts#listOrders") == []               # suffix only at a path boundary
    assert {c["fqn"] for c in Q.impact(st, "a/app.ts#listOrders")["callers"]} == {"useA"}


def test_class_member_specs(db):
    st = GraphStore(db)
    assert Q.resolve_targets(st, "src/svc.ts#OrderService.create") == [CREATE]
    assert Q.resolve_targets(st, "svc.ts#create") == [CREATE]
    assert {c["fqn"] for c in Q.impact(st, "src/svc.ts#OrderService.create")["callers"]} == {"make"}


def test_repeated_names_in_one_file(db):
    st = GraphStore(db)
    ids = [r["id"] for r in st.q("SELECT id FROM nodes WHERE file = 'src/codec.ts' AND kind = 'function' "
                                  "AND id LIKE '%decode%' ORDER BY line")]
    assert len(ids) == 2 and ids[1].endswith("~2"), ids
    assert Q.resolve_targets(st, "codec.ts#decode") == ids              # every declaration of the name in that file
    assert Q.resolve_targets(st, "codec.ts#" + ids[1].split("#", 1)[1]) == [ids[1]]


def test_cli_and_mcp(db):
    p = subprocess.run([sys.executable, "-m", "codegraph.cli", "impact", "a/app.ts#listOrders", "--db", str(db), "--no-paths"],
                       cwd=ROOT, capture_output=True, text=True)
    assert p.returncode == 0 and f"targets: ['{A}']" in p.stdout and "useA" in p.stdout and "useB" not in p.stdout
    import codegraph.mcp_server as M
    old = M.STATE["db"]
    M.STATE["db"] = str(db)
    try:
        assert "transitive callers: 1" in M.impact("src/svc.ts#OrderService.create")
        assert "useB" in M.reaches(["b/app.ts#listOrders"]) and "useA" not in M.reaches(["b/app.ts#listOrders"])
    finally:
        M.STATE["db"] = old
