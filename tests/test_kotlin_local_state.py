"""Compose state held in a local delegate (#105, #88): `var count by remember { mutableIntStateOf(0) }`,
`by rememberSaveable { mutableStateOf("") }`, `by vm.state.collectAsStateWithLifecycle()` and `by mutableStateOf()`
are `field:<function>.<name>` nodes (`property: state`, `hook`, `local`); bare reads and writes inside the function
(including lambdas such as `onClick = { count++ }`) are READS_PROP / WRITES_PROP. A plain local of the same name in
another function binds nothing."""
import json
import sqlite3
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from cg_code_graph.indexer import index_project  # noqa: E402

pytest.importorskip("tree_sitter_kotlin")

SRC = """\
package app

import androidx.compose.runtime.*

class CounterViewModel {
    val state = MutableStateFlow(0)
}

@Composable
fun CounterScreen(vm: CounterViewModel) {
    var count by remember { mutableIntStateOf(0) }
    var name by rememberSaveable { mutableStateOf("") }
    val total by vm.state.collectAsStateWithLifecycle()
    var shown by mutableStateOf(false)
    Button(onClick = { count++ }) {
        Text(label(count, total))
    }
    TextField(value = name, onValueChange = { name = it })
    if (shown) Dialog(onDismiss = { shown = false })
    Text("$count of $total")
}

@Composable
fun Other() {
    val count = 3
    Text(label(count, 0))
}

fun label(a: Int, b: Int): String = ""
"""


@pytest.fixture(scope="module")
def g():
    d = Path(tempfile.mkdtemp(prefix="cg-kt-local-state-"))
    (d / "p/src/main/kotlin/app").mkdir(parents=True)
    (d / "p/src/main/kotlin/app/Counter.kt").write_text(SRC)
    index_project(d / "p", d / "g.db", "kls")
    return sqlite3.connect(d / "g.db")


def test_local_state_nodes(g):
    n = {i: json.loads(a or "{}") for i, a in g.execute("select id, attrs from nodes where kind='field'")}
    f = "field:app.CounterScreen."
    assert n[f + "count"] == {"property": "state", "hook": "mutableIntStateOf", "binding": "var", "local": True}
    assert n[f + "name"]["hook"] == "mutableStateOf" and n[f + "total"]["hook"] == "collectAsStateWithLifecycle"
    assert n[f + "shown"]["binding"] == "var"
    assert not [i for i in n if i.startswith("field:app.Other.")]


def test_local_state_reads_and_writes(g):
    e = {(d.rsplit(".", 1)[-1], k, ln) for s, d, k, ln in g.execute(
        "select src, dst, kind, line from edges where kind in ('READS_PROP','WRITES_PROP') and dst like 'field:app.CounterScreen.%'")}
    assert {("count", "READS_PROP", 15), ("count", "WRITES_PROP", 15), ("count", "READS_PROP", 16),
            ("total", "READS_PROP", 16), ("name", "READS_PROP", 18), ("name", "WRITES_PROP", 18),
            ("shown", "READS_PROP", 19), ("shown", "WRITES_PROP", 19),
            ("count", "READS_PROP", 20), ("total", "READS_PROP", 20)} <= e, e
    srcs = {s for (s,) in g.execute("select src from edges where dst like 'field:app.CounterScreen.%' and kind like '%PROP'")}
    assert srcs == {"function:app.CounterScreen"}
