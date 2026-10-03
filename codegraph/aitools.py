"""`cg tools` / MCP `llm_tools` (#66): LLM tools and MCP primitives (#31 endpoints of the llm_tool / mcp_tool /
mcp_resource / mcp_prompt protocols) with their handlers, the tables a handler writes or reads (depth-limited), the
agents offering them and who invokes them; agents; agent loops dispatching by a runtime name (dynamic_dispatch).

Flags per tool: the #31 checks (no_receiver: offered to the model / called by a client, no handler; no_sender: a
handler nothing offers or calls) plus name_collision (two handlers for one name in one toolset)."""
from __future__ import annotations

import fnmatch
import json
from collections import defaultdict

AI_PROTOCOLS = ("llm_tool", "mcp_tool", "mcp_resource", "mcp_prompt")


def _reach_tables(st, fn, depth=3):
    seen, frontier, out = {fn}, [fn], set()
    for _ in range(depth):
        nxt = []
        for s in frontier:
            for r in st.q("SELECT dst, kind FROM edges WHERE src=? AND kind IN ('CALLS','WRITES_TABLE','READS_TABLE','INSTANTIATES')", (s,)):
                if r["dst"].startswith("table:"):
                    out.add(f"{r['dst'][6:]} ({'write' if r['kind'] == 'WRITES_TABLE' else 'read'})")
                elif r["dst"] not in seen:
                    seen.add(r["dst"]); nxt.append(r["dst"])
        frontier = nxt
    return sorted(out)


def tools(st, pattern: str | None = None, framework: str | None = None, unmatched: bool = False, agent: str | None = None,
          max_items: int = 200) -> dict:
    from .protocols.view import protocols
    eps = []
    for p in AI_PROTOCOLS:
        eps += protocols(st, protocol=p, max_items=100000)["endpoints"]
    attrs = {r["id"]: json.loads(r["attrs"] or "{}") for r in
             st.q("SELECT id, attrs FROM nodes WHERE kind='endpoint' AND id LIKE 'endpoint:llm_tool:%' OR id LIKE 'endpoint:mcp_%'")}
    agents = defaultdict(list)
    agent_rows = {r["id"]: r for r in st.q("SELECT id, name, file, line, attrs FROM nodes WHERE kind='agent'")}
    for r in st.q("SELECT src, dst FROM edges WHERE kind='OFFERS_TOOL'"):
        agents[r["dst"]].append(agent_rows[r["src"]]["name"] if r["src"] in agent_rows else r["src"])
    out = []
    summary = defaultdict(lambda: defaultdict(int))
    for e in eps:
        a = attrs.get(e["id"], {})
        fw = a.get("framework") or ("mcp" if e["protocol"].startswith("mcp_") else "custom")
        e = {**e, "framework": fw, "agents": sorted(set(agents.get(e["id"], []))),
             "description": a.get("description"), "params": a.get("params"), "schema_source": a.get("schema_source"),
             "toolset": a.get("toolset")}
        per_file = defaultdict(set)            # one toolset ~ one module: the same name in separate examples is no collision
        for r in e["receivers"]:
            per_file[(r.get("at") or "").rsplit(":", 1)[0]].add(r["handler"])
        if any(len(v) > 1 for v in per_file.values()) and e["protocol"] != "mcp_resource":
            e["checks"] = [*e["checks"], "name_collision"]
        s = summary[f"{e['protocol']}/{fw}"]
        s["tools"] += 1
        s["handled"] += bool(e["receivers"])
        s["offered_or_called"] += bool(e["senders"] or e["matches"])
        for c in e["checks"]:
            s[c] += 1
        if framework and fw != framework:
            continue
        if agent and not any(fnmatch.fnmatchcase(x, agent) or agent in x for x in e["agents"]):
            continue
        if unmatched and not e["checks"]:
            continue
        if pattern and not (fnmatch.fnmatchcase(e["name"], pattern) or pattern.lower() in e["name"].lower() or e["id"] == pattern):
            continue
        out.append(e)
    for e in out[:max_items]:
        e["reaches_tables"] = sorted({t for r in e["receivers"] for t in _reach_tables(st, r["handler"])})
    dyn = []
    for r in st.q("SELECT id, file, attrs FROM nodes WHERE attrs LIKE '%llm_dynamic_dispatch%'"):
        for d in json.loads(r["attrs"]).get("llm_dynamic_dispatch", []):
            dyn.append({"fn": r["id"], "at": f"{r['file']}:{d['line']}", "expr": d["expr"]})
    ags = []
    for aid, r in sorted(agent_rows.items()):
        aa = json.loads(r["attrs"] or "{}")
        ags.append({"id": aid, "name": r["name"], "at": f"{r['file']}:{r['line']}", "framework": aa.get("framework"),
                    "model": aa.get("model"), "tools": [x["dst"] for x in st.q("SELECT dst FROM edges WHERE src=? AND kind='OFFERS_TOOL'", (aid,))],
                    "handoffs": [x["dst"] for x in st.q("SELECT dst FROM edges WHERE src=? AND kind='HANDS_OFF_TO'", (aid,))]})
    models = []
    for r in st.q("SELECT id, file, attrs FROM nodes WHERE attrs LIKE '%llm_calls%'"):
        for c in json.loads(r["attrs"]).get("llm_calls", []):
            models.append({"fn": r["id"], "at": f"{r['file']}:{c.get('line')}", **{k: v for k, v in c.items() if k != "line"}})
    return {"summary": {k: dict(v) for k, v in sorted(summary.items())}, "tools": out[:max_items], "selected": len(out),
            "agents": ags, "dynamic_dispatch": dyn, "model_calls": models,
            "filters": {"pattern": pattern, "framework": framework, "unmatched": unmatched, "agent": agent}}


def render_tools(res: dict, max_items: int = 60) -> str:
    if not res["summary"] and not res["agents"] and not res["dynamic_dispatch"]:
        return "no LLM tools, MCP servers or agents in this graph (see docs/ai-tools.md for the frameworks covered)"
    L = [f"{'protocol/framework':28} {'tools':>6} {'handled':>8} {'offered':>8}  checks"]
    for k, s in res["summary"].items():
        ck = ", ".join(f"{c} {n}" for c, n in s.items() if c not in ("tools", "handled", "offered_or_called")) or "-"
        L.append(f"{k:28} {s['tools']:>6} {s['handled']:>8} {s['offered_or_called']:>8}  {ck}")
    if any(res["filters"].values()) or res["selected"] <= 30:
        L.append("")
        for e in res["tools"][:max_items]:
            fl = f"  ! {', '.join(e['checks'])}" if e["checks"] else ""
            L.append(f"[{e['protocol']}] {e['name']}  ({e['framework']}{', ' + e['schema_source'] if e.get('schema_source') else ''}){fl}")
            for r in e["receivers"]:
                L.append(f"    handler  {r['handler'].split(':', 1)[1]} @ {r['at']} [{r['confidence']}]")
            for s in e["senders"]:
                L.append(f"    {'offered' if e['protocol'] == 'llm_tool' else 'called'} by {s['fn'].split(':', 1)[1]} @ {s['at']} [{s['confidence']}]")
            for m in e["matches"]:
                L.append(f"    {'<-' if m.get('dir') == 'in' else '->'} {m['endpoint']} [{m.get('confidence')}]")
            if e["agents"]:
                L.append(f"    agents   {', '.join(e['agents'])}")
            if e.get("reaches_tables"):
                L.append(f"    tables   {', '.join(e['reaches_tables'])}")
        if res["selected"] > max_items:
            L.append(f"... {res['selected'] - max_items} more")
    if res["agents"]:
        L.append("")
        L.append("agents:")
        for a in res["agents"][:max_items]:
            L.append(f"  {a['name']} ({a['framework']}{', model ' + a['model'] if a.get('model') else ''}) @ {a['at']}: "
                     f"{len(a['tools'])} tools{', hands off to ' + ', '.join(h.split(':', 1)[1] for h in a['handoffs']) if a['handoffs'] else ''}")
    if res["dynamic_dispatch"]:
        L.append("")
        L.append("dynamic_dispatch (tool chosen by a runtime name; not linked):")
        for d in res["dynamic_dispatch"][:max_items]:
            L.append(f"  {d['fn'].split(':', 1)[1]} @ {d['at']}: {d['expr']}")
    if res["model_calls"]:
        L.append("")
        L.append(f"model calls: {len(res['model_calls'])} (" + ", ".join(sorted({f"{m.get('provider')}:{m.get('model') or m.get('base_url')}" for m in res['model_calls']}))[:300] + ")")
    return "\n".join(L)
