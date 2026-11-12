"""CLI ergonomics: rejected flags (#243), unknown commands (#248), compact text (#244)."""
import contextlib
import io
import json
from pathlib import Path

import pytest

from cg_code_graph.cli import main
from cg_code_graph.core.model import Edge, Node
from cg_code_graph.core.store import GraphStore


def _run(argv, monkeypatch, env=None):
    monkeypatch.delenv("CG_OUTPUT", raising=False)
    for key, value in (env or {}).items():
        monkeypatch.setenv(key, value)
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = main(list(argv))
        except SystemExit as exc:
            code = exc.code
    return code, out.getvalue(), err.getvalue()


def test_rejected_flags_name_the_real_option(monkeypatch):
    code, _out, err = _run(["search", "ParcelDesk", "--db", "unused.db", "-k", "class"], monkeypatch)
    assert code == 2
    assert err.strip() == "cg: error: unknown -k; did you mean --kind?"
    assert "usage:" not in err and "choose from" not in err

    code, _out, err = _run(["snippet", "ParcelDesk.dispatch", "--db", "unused.db", "-A", "40"], monkeypatch)
    assert code == 2
    assert "unknown -A; did you mean --context or --max-lines?" in err
    assert "usage:" not in err

    code, _out, err = _run(["snippet", "ParcelDesk.dispatch", "--db", "unused.db", "-C", "4"], monkeypatch)
    assert code == 2 and "unknown -C; did you mean --context?" in err

    code, _out, err = _run(["node", "ParcelDesk.dispatch", "--db", "unused.db", "--depth", "2"], monkeypatch)
    assert code == 2
    assert err.strip() == "cg: error: unknown --depth; did you mean --max-depth?"

    code, _out, err = _run(["search", "ParcelDesk", "--db", "unused.db", "--depth", "2"], monkeypatch)
    assert code == 2
    assert "unknown --depth; did you mean --max-depth (on " in err and err.strip().endswith(")?")
    assert "impact" in err and "search" not in err

    code, _out, err = _run(["search", "ParcelDesk", "--db", "unused.db", "--name", "lumen-ledger"], monkeypatch)
    assert code == 2
    assert "unknown --name; did you mean --db?" in err
    assert "index" in err and "refresh" in err and "watch" in err
    assert "usage:" not in err


def test_unknown_flag_without_a_mapping_stays_short(monkeypatch):
    code, _out, err = _run(["search", "ParcelDesk", "--db", "unused.db", "--not-a-flag"], monkeypatch)
    assert code == 2
    assert "unrecognized arguments: --not-a-flag" in err
    assert "see `cg search --help`" in err
    assert "choose from" not in err and "api-calls" not in err


def test_missing_required_flag_still_shows_command_usage(monkeypatch):
    code, _out, err = _run(["search", "ParcelDesk"], monkeypatch)
    assert code == 2
    assert "usage:" in err and "--db" in err and "required" in err


def test_unknown_command_suggests_impact_node_and_readers(monkeypatch):
    code, _out, err = _run(["callers", "function:ParcelDesk.dispatch", "--db", "unused.db"], monkeypatch)
    assert code == 2
    assert err.strip() == "cg: error: unknown command 'callers'; try: cg impact, cg node, or cg readers"
    assert "choose from" not in err and "api-calls" not in err

    code, _out, err = _run(["impct", "ParcelDesk.dispatch"], monkeypatch)
    assert code == 2 and "try: cg impact" in err

    code, _out, err = _run(["zzzzqqq"], monkeypatch)
    assert code == 2 and "run `cg --help` for commands" in err
    assert "api-calls" not in err


def test_help_names_compact_and_budget(monkeypatch):
    for cmd in ("search", "snippet", "impact"):
        code, out, err = _run([cmd, "-h"], monkeypatch)
        assert code == 0, err
        text = " ".join(out.split())
        assert "--compact" in text and "cg explore --budget" in text
    code, out, err = _run(["explore", "-h"], monkeypatch)
    assert code == 0, err
    assert "--budget" in out and "search, snippet and impact" in " ".join(out.split())


def _ledger(tmp_path: Path) -> Path:
    src = tmp_path / "src" / "parcel" / "desk.py"
    src.parent.mkdir(parents=True)
    body = ["def dispatch_parcel(order):"]
    body += [f"    step_{i} = {i}" for i in range(120)]
    body.append("    return step_119")
    src.write_text("\n".join(body) + "\n")
    st = GraphStore.create(tmp_path / "g.db")
    target = Node("function:desk.dispatch_parcel", "function", "dispatch_parcel", fqn="desk.dispatch_parcel",
                  file="src/parcel/desk.py", line=1, end_line=len(body), lang="python", module="desk")
    nodes = [target]
    edges = []
    long = "vendor/third_party/generated/snapshots/region/north/warehouse/service"
    for i in range(12):
        caller = Node(
            f"function:desk.helper_{i}", "function", f"dispatch_parcel_helper_{i}",
            fqn=f"desk.dispatch_parcel_helper_{i}", file=f"{long}/helper_{i}.py", line=10 + i,
            lang="python", module="desk", entry_kind="command" if i < 6 else None)
        nodes.append(caller)
        edges.append(Edge(caller.id, target.id, "CALLS", caller.file, 3, "exact"))
    st.write(nodes, edges)
    st.set_meta(root=str(tmp_path), project="lumen-ledger")
    st.db.close()
    return tmp_path / "g.db"


@pytest.fixture()
def ledger(tmp_path):
    return _ledger(tmp_path)


def test_compact_search_is_short_and_default_is_unchanged(ledger, monkeypatch):
    code, default, err = _run(["search", "dispatch_parcel", "--db", str(ledger)], monkeypatch)
    assert code in (0, None), err
    assert "vendor/third_party/generated" in default
    assert "cg explore --budget" not in default
    assert sum(1 for line in default.splitlines() if line.startswith("function")) == 13

    code, compact, err = _run(["search", "dispatch_parcel", "--db", str(ledger), "--compact"], monkeypatch)
    assert code in (0, None), err
    assert "vendor/third_party" not in compact
    assert ".../warehouse/service/helper_" in compact
    assert "5 more, raise --limit" in compact
    assert "cg explore --budget" in compact
    assert len(compact) < len(default)

    code, via_env, err = _run(["search", "dispatch_parcel", "--db", str(ledger)], monkeypatch,
                              env={"CG_OUTPUT": "compact"})
    assert via_env == compact

    code, ignored, err = _run(["search", "dispatch_parcel", "--db", str(ledger)], monkeypatch,
                              env={"CG_OUTPUT": "full"})
    assert ignored == default

    code, limited, err = _run(["search", "dispatch_parcel", "--db", str(ledger), "--compact", "--limit", "2"],
                              monkeypatch)
    assert "11 more, raise --limit" in limited
    assert sum(1 for line in limited.splitlines() if line.startswith("function")) == 2

    code, raw, err = _run(["search", "dispatch_parcel", "--db", str(ledger), "--json"], monkeypatch,
                          env={"CG_OUTPUT": "compact"})
    assert code in (0, None), err
    doc = json.loads(raw)
    assert len(doc["nodes"]) == 13
    assert any("vendor/third_party/generated" in n["file"] for n in doc["nodes"])


def test_compact_snippet_and_impact(ledger, monkeypatch):
    spec = "desk.dispatch_parcel"
    code, default, err = _run(["snippet", spec, "--db", str(ledger)], monkeypatch)
    assert code == 0, err
    assert default.startswith("src/parcel/desk.py:1-")
    assert "step_100" in default and "cg explore --budget" not in default

    code, compact, err = _run(["snippet", spec, "--db", str(ledger), "--compact"], monkeypatch)
    assert code == 0, err
    assert compact.startswith("src/parcel/desk.py:1-")
    assert "step_100" not in compact
    assert "truncated" in compact and "--max-lines" in compact
    assert "cg explore --budget" in compact
    assert len(compact) < len(default) / 2

    code, via_env, err = _run(["snippet", spec, "--db", str(ledger)], monkeypatch, env={"CG_OUTPUT": "Compact"})
    assert via_env == compact

    code, full, err = _run(["impact", spec, "--db", str(ledger)], monkeypatch)
    assert code in (0, None), err
    assert "path:" in full and "vendor/third_party" in full

    code, compact, err = _run(["impact", spec, "--db", str(ledger), "--compact"], monkeypatch)
    assert code in (0, None), err
    assert "path:" not in compact and "vendor/third_party" not in compact
    assert "4 more callers, drop --compact" in compact
    assert "1 more entry points, drop --compact" in compact
    assert "cg explore --budget" in compact
    assert len(compact) < len(full)
