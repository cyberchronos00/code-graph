"""One-call answer: resolve a question to a few symbols, then source, entry points, call paths and blast radius."""
from __future__ import annotations

import re

from . import query as Q
from .core.store import GraphStore

_STOP = frozenset({
    "how", "does", "do", "did", "what", "where", "when", "why", "which", "who", "the", "a", "an",
    "to", "of", "in", "on", "for", "from", "with", "and", "or", "is", "are", "be", "it", "this",
    "that", "work", "works", "working", "happen", "happens", "code", "function", "method",
})
_FILE_RE = re.compile(r"\S+\.(?:py|ts|tsx|js|jsx|vue|php|dart|kt|swift|rs|c|cc|cpp|h)\b")
_WORD_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_EXCLUDE = frozenset({"module", "file", "field", "column", "constant"})
_WEIGHT = {k: 3 for k in ("method", "function", "route")}
_WEIGHT.update({k: 2 for k in ("class", "page", "component", "table")})
_DATA_KINDS = frozenset({"table", "column", "connection", "config", "env"})
_TEST_FILE = re.compile(r"(?:^|/)(?:tests?|__tests__|spec)/|(?:^|/)test_[^/]*\.py$|_tests?\.py$|(?:^|/)conftest\.py$|"
                        r"\.(?:test|spec)\.[cm]?[jt]sx?$")
_WRITE = ("WRITES_TABLE", "WRITES_COLUMN")
_READ = ("READS_TABLE", "READS_COLUMN")


def _stem(word: str) -> str:
    for suf in ("ing", "ed", "es", "s"):
        if word.endswith(suf) and len(word) - len(suf) >= 3:
            return word[:-len(suf)]
    return word


def _clamp_budget(budget_tokens: int) -> int:
    return min(20000, max(500, int(budget_tokens)))


def _symbols_of(st: GraphStore, ids: list[str], score: int) -> list[dict]:
    out = []
    for nid in ids:
        n = st.node(nid)
        if not n:
            continue
        out.append({"id": nid, "kind": n["kind"], "file": n["file"], "line": n["line"], "score": score})
    return out


def _rows(st: GraphStore, ids: list[str]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for i in range(0, len(ids), 400):
        chunk = ids[i:i + 400]
        q = ",".join("?" * len(chunk))
        for row in st.q(f"SELECT id, kind, name, fqn, file, entry_kind FROM nodes WHERE id IN ({q})", chunk):
            out[row["id"]] = dict(row)
    return out


def _is_test(row: dict) -> bool:
    return (row.get("kind") == "test" or row.get("entry_kind") == "test"
            or bool(_TEST_FILE.search((row.get("file") or "").replace("\\", "/"))))


def _name_of(row: dict, kind: str) -> str:
    """The symbol's own name, lower-cased; a method keeps its class (`Order.place`)."""
    name = (row.get("name") or "").lower()
    fqn = row.get("fqn") or ""
    if kind == "method" and "." in fqn:
        parts = fqn.rsplit(".", 2)
        if len(parts) >= 2:
            name = f"{parts[-2].lower()}.{name}"
    return name


def resolve(st: GraphStore, query: str, max_symbols: int) -> tuple[list[dict], list[str]]:
    """Ranked symbols for a spec, a file mention, or free text. Stems are the words that were searched."""
    query = query.strip()
    ids = (Q.resolve_targets(st, query) or Q.route_targets(st, query)) if query else []
    if ids:
        meta = _rows(st, ids)
        # a bare word that names a test fixture or a module is not what a question means: fall through
        kept = [i for i in ids if i in meta and meta[i]["kind"] not in ("module", "file") and not _is_test(meta[i])]
        if 1 <= len(kept) <= max_symbols:
            return _symbols_of(st, kept, 100), []
    files = [tok for tok in query.split() if _FILE_RE.fullmatch(tok)]
    if files:
        found: list[str] = []
        seen: set[str] = set()
        for tok in files:
            for sid in Q.file_symbols(st, tok):
                if sid not in seen:
                    seen.add(sid)
                    found.append(sid)
                if len(found) >= max_symbols:
                    return _symbols_of(st, found, 50), []
        if found:
            return _symbols_of(st, found, 50), []
    explicit = bool(query) and not any(c.isspace() for c in query) and any(c in query for c in ".:/\\")
    if explicit and 1 <= len(ids) <= max_symbols:   # an explicit spec (a test, a module) still answers
        return _symbols_of(st, ids, 100), []
    stems: list[str] = []
    seen_stems: set[str] = set()
    for word in (w.lower() for w in _WORD_RE.findall(query)):
        if word in _STOP:
            continue
        stem = _stem(word)
        if stem not in seen_stems:
            seen_stems.add(stem)
            stems.append(stem)
    if not stems:
        return [], []
    cands: dict[str, dict] = {}
    for stem in stems:
        for node in Q.search(st, stem, limit=100)["nodes"]:
            cands.setdefault(node["id"], node)
    meta = _rows(st, list(cands))
    scored = []
    for nid, node in cands.items():
        kind = node["kind"]
        if kind in _EXCLUDE or _is_test(meta.get(nid) or {"kind": kind, "file": node.get("file")}):
            continue
        name = _name_of(meta.get(nid) or {}, kind)
        in_name = sum(1 for stem in stems if stem in name)
        in_path = sum(1 for stem in stems if stem not in name and stem in nid.lower())
        # a word in the symbol's own name counts more than one that only appears in its module path
        score = 10 * in_name + 4 * in_path + _WEIGHT.get(kind, 0)
        if score < 10:
            continue
        scored.append((score, nid, node))
    scored.sort(key=lambda item: (-item[0], len(item[1]), item[1]))
    symbols = [{"id": nid, "kind": node["kind"], "file": node["file"], "line": node["line"], "score": score}
               for score, nid, node in scored[:max_symbols]]
    return symbols, stems


def _table_name(item: dict) -> str:
    raw = item["id"].split(":", 1)[-1]
    if item.get("kind") == "column" or item["id"].startswith("column:"):
        return raw.split(".", 1)[0]
    return raw


def _sink_name(kind: str, item: dict) -> str:
    raw = item["id"].split(":", 1)[-1]
    if kind == "job":
        return raw.rsplit(".", 1)[-1]
    return raw


def _accumulate(bucket: list[dict], seen: set[str], name: str, confidence: str) -> None:
    if name in seen:
        return
    seen.add(name)
    bucket.append({"name": name, "confidence": confidence})


def _blast_from_downstream(ds: dict) -> dict:
    written: list[dict] = []
    read: list[dict] = []
    seen_w: set[str] = set()
    seen_r: set[str] = set()
    sinks = ds.get("sinks") or {}
    for item in list(sinks.get("table") or []) + list(sinks.get("column") or []):
        path = item.get("path") or []
        hop = path[-1]["kind"] if path else ""
        name = _table_name(item)
        conf = item.get("path_confidence") or "exact"
        if hop in _WRITE or hop.startswith("WRITES"):
            _accumulate(written, seen_w, name, conf)
        elif hop in _READ or hop.startswith("READS"):
            _accumulate(read, seen_r, name, conf)
    def side(kind: str) -> list[dict]:
        return [{"name": _sink_name(kind, item), "confidence": item.get("path_confidence") or "exact"}
                for item in (sinks.get(kind) or [])]

    return {"tables_written": written, "tables_read": read, "env": side("env"), "jobs": side("job"),
            "connections": side("connection")}


def _empty_blast() -> dict:
    return {"callers": None, "entry_points": None, "tables_written": [], "tables_read": [],
            "env": [], "jobs": [], "connections": []}


def _followups(symbols: list[dict]) -> list[str]:
    out = []
    for sym in symbols:
        short = Q.short_id(sym["id"])
        if sym["kind"] == "route" or sym["kind"] in _DATA_KINDS:
            out.append(f"downstream('{short}')  snippet('{sym['id']}', context=5)")
        else:
            out.append("  ".join((
                f"impact('{short}')",
                f"downstream('{short}')",
                f"tests_covering('{short}')",
                f"snippet('{sym['id']}', context=5)",
            )))
    return out


def _collect(st: GraphStore, symbols: list[dict]) -> tuple[list[dict], list[dict], dict]:
    entry_points: list[dict] = []
    paths: list[dict] = []
    blast: dict[str, dict] = {}
    for sym in symbols:
        sid, kind = sym["id"], sym["kind"]
        if kind in _DATA_KINDS:
            blast[sid] = _empty_blast()
            continue
        if kind == "route":
            info = _blast_from_downstream(Q.downstream(st, sid))
            info["callers"] = None
            info["entry_points"] = None
            blast[sid] = info
            continue
        imp = Q.impact(st, sid)
        info = _blast_from_downstream(Q.downstream(st, sid))
        kept = imp.get("entry_points") or []
        info["callers"] = len(imp.get("callers") or [])
        info["entry_points"] = len(kept)
        blast[sid] = info
        for entry in kept[:6]:
            entry_points.append({
                "symbol": sid,
                "entry_kind": entry.get("entry_kind"),
                "name": entry.get("name"),
                "path_confidence": entry.get("path_confidence"),
            })
        first = next((e for e in kept[:6] if e.get("path")), None)
        if first:
            paths.append({"via": "entry", "symbol": sid, "hops": first["path"]})
    calls = 0
    ids = [s["id"] for s in symbols]
    for src in ids:
        for dst in ids:
            if src == dst:
                continue
            if calls >= 6:
                break
            calls += 1
            hops = Q.path_between(st, src, dst)
            if hops and hops not in [p["hops"] for p in paths]:
                paths.append({"via": "between", "symbol": src, "to": dst, "hops": hops})
        if calls >= 6:
            break
    return entry_points, paths, blast


def _body(res: dict) -> str:
    return "\n".join(_head(res) + _source_lines(res) + _next_lines(res))


def _head(res: dict) -> list[str]:
    symbols = res["symbols"]
    shown = ", ".join(f"{Q.short_id(s['id'])} ({s['kind']})" for s in symbols)
    noun = "symbol" if len(symbols) == 1 else "symbols"
    lines = [f"explore {res['query']!r}: {len(symbols)} {noun}: {shown}", "## entry points"]
    if res["entry_points"]:
        for entry in res["entry_points"]:
            lines.append(f"  {entry['entry_kind']}  {entry['name']} -> {Q.short_id(entry['symbol'])}  "
                         f"conf={entry['path_confidence']}")
    else:
        lines.append("  (none)")
    lines.append("## call paths")
    if res["paths"]:
        for path in res["paths"]:
            lines.append("  " + _fmt_hops(path.get("hops") or []))
    else:
        lines.append("  (none)")
    lines.append("## blast radius")
    for sym in symbols:
        lines.append(_blast_line(sym, res["blast"].get(sym["id"]) or _empty_blast()))
    return lines


def _fmt_hops(hops: list[dict]) -> str:
    if not hops:
        return "(none)"
    parts = [Q.short_id(hops[0].get("from"))]
    for hop in hops:
        parts.append(f"-{hop['kind']}[{hop['confidence']} @ {hop['at']}]-> {Q.short_id(hop.get('to'))}")
    return " ".join(parts)


def _count(n: int, singular: str) -> str:
    return f"{n} {singular if n == 1 else singular + 's'}"


def _grouped(label: str, items: list[dict]) -> str:
    groups: list[tuple[str, list[str]]] = []
    for item in items:
        if groups and groups[-1][0] == item["confidence"]:
            groups[-1][1].append(item["name"])
        else:
            groups.append((item["confidence"], [item["name"]]))
    if len(groups) == 1:
        conf, names = groups[0]
        return f"{label} {', '.join(names)} ({conf})"
    return "; ".join(f"{label} {', '.join(names)} ({conf})" for conf, names in groups)


def _blast_line(sym: dict, info: dict) -> str:
    bits = []
    if info.get("callers") is not None:
        bits.append(f"{_count(info['callers'], 'caller')}, {_count(info['entry_points'] or 0, 'entry point')}")
    for key, label in (("tables_written", "writes"), ("tables_read", "reads"), ("env", "env"),
                       ("jobs", "jobs"), ("connections", "connections")):
        if info.get(key):
            bits.append(_grouped(label, info[key]))
    if not bits:
        return f"  {Q.short_id(sym['id'])}: {sym['kind']}"
    return f"  {Q.short_id(sym['id'])}: " + "; ".join(bits)


def _source_lines(res: dict) -> list[str]:
    lines = ["## source"]
    by_file: dict[str, list[dict]] = {}
    order: list[str] = []
    for src in res.get("sources") or []:
        if src["file"] not in by_file:
            order.append(src["file"])
            by_file[src["file"]] = []
        by_file[src["file"]].append(src)
    for path in order:
        lines.append(f"### {path}")
        for src in sorted(by_file[path], key=lambda s: (s.get("start") or 0, s["id"])):
            body = src.get("lines") or []
            width = max((len(str(no)) for no, _ in body), default=1)
            for no, text in body:
                lines.append(f"  {no:>{width}}| {text}")
            more = src.get("truncated") or 0
            if more:
                lines.append(f"  … {more} more lines: snippet({src['id']!r}, max_lines={len(body) + more})")
    skipped = res.get("skipped_sources") or []
    if skipped:
        hints = ", ".join(f"snippet({sid!r}, max_lines=80)" for sid in skipped)
        lines.append(f"  skipped: {hints}")
    if len(lines) == 1:
        lines.append("  (none)")
    return lines


def _next_lines(res: dict) -> list[str]:
    lines = ["## next"]
    if res.get("next"):
        lines.extend(f"  {item}" for item in res["next"])
    over = res.get("over_budget") or []
    if over:
        shown = "  ".join(f"explore('{Q.short_id(sid)}')" for sid in over[:3])
        more = f"  (+{len(over) - 3} more; raise budget_tokens)" if len(over) > 3 else ""
        lines.append(f"  over budget, not expanded: {shown}{more}")
    if len(lines) == 1:
        lines.append("  (none)")
    return lines


def _budget_line(res: dict) -> str:
    budget = res.get("budget") or {}
    return f"budget: {budget.get('used', 0)}/{budget.get('limit', 0)} tokens"


def _fixed_length(res: dict, budget: int) -> int:
    """Characters of the answer with every source skipped (the worst case _fit_sources can end at)."""
    saved = res["sources"], res["skipped_sources"]
    res["sources"], res["skipped_sources"] = [], [s["id"] for s in res["symbols"]]
    try:
        return len(_body(res)) + 1 + len(f"budget: {budget}/{budget} tokens")
    finally:
        res["sources"], res["skipped_sources"] = saved


def _drop_symbol(res: dict) -> None:
    sid = res["symbols"].pop()["id"]
    res["over_budget"].insert(0, sid)
    res["entry_points"] = [e for e in res["entry_points"] if e["symbol"] != sid]
    res["paths"] = [p for p in res["paths"] if p["symbol"] != sid and p.get("to") != sid]
    res["blast"].pop(sid, None)
    res["next"] = _followups(res["symbols"])


def _fit_fixed(res: dict, budget: int) -> None:
    """Drop the lowest-ranked symbols (then extra entry points and paths of the last one) until the
    sections other than source fit the budget, leaving room for some source of the top symbol."""
    limit_chars = budget * 4 - min(600, budget)
    while len(res["symbols"]) > 1 and _fixed_length(res, budget) > limit_chars:
        _drop_symbol(res)
    while len(res["entry_points"]) > 1 and _fixed_length(res, budget) > limit_chars:
        res["entry_points"].pop()
    while len(res["paths"]) > 1 and _fixed_length(res, budget) > limit_chars:
        res["paths"].pop()


def _fit_sources(st: GraphStore, res: dict, budget: int) -> None:
    """Keep header, paths, blast and next steps; fill source until the token budget is spent."""
    limit_chars = budget * 4
    snippets = [Q.snippet(st, sym["id"], max_lines=80) for sym in res["symbols"]]
    res["sources"] = []
    res["skipped_sources"] = []

    def length(pending: list[str] = ()) -> int:
        saved = res["skipped_sources"]
        res["skipped_sources"] = saved + list(pending)
        try:
            body = _body(res)
            res["budget"]["used"] = len(body) // 4
            return len(body) + 1 + len(_budget_line(res))
        finally:
            res["skipped_sources"] = saved

    for k, sn in enumerate(snippets):
        # later symbols may still be skipped: count their hint line now, so this source is not dropped later
        pending = [sym["id"] for sym in res["symbols"][k + 1:]]
        if sn.get("status") != "ok" or not sn.get("lines"):
            if sn.get("id"):
                res["skipped_sources"].append(sn["id"])
            continue
        room = limit_chars - length(pending)
        cap = min(80, len(sn["lines"]), max(0, room // 4))
        chosen = None
        for count in range(1, cap + 1):
            truncated = (sn.get("truncated") or 0) + (len(sn["lines"]) - count)
            trial = {
                "id": sn["id"], "file": sn["file"], "start": sn["start"], "end": sn["end"],
                "lines": sn["lines"][:count], "truncated": truncated,
            }
            res["sources"].append(trial)
            ok = length(pending) <= limit_chars
            res["sources"].pop()
            if not ok:
                break
            chosen = trial
        if chosen is None:
            res["skipped_sources"].append(sn["id"])
            continue
        res["sources"].append(chosen)
    while res["sources"] and length() > limit_chars:
        dropped = res["sources"].pop()
        res["skipped_sources"].append(dropped["id"])
    res["budget"]["used"] = len(_body(res)) // 4


def explore(st: GraphStore, query: str, budget_tokens: int = 3000, max_symbols: int = 4) -> dict:
    """Resolve `query` and pack source, entry points, call paths and blast radius into `budget_tokens`."""
    budget = _clamp_budget(budget_tokens)
    max_symbols = min(20, max(1, int(max_symbols)))
    symbols, stems = resolve(st, query, max_symbols)
    res = {
        "query": query,
        "symbols": symbols,
        "entry_points": [],
        "paths": [],
        "blast": {},
        "sources": [],
        "skipped_sources": [],
        "next": [],
        "budget": {"limit": budget, "used": 0},
        "stems": stems,
        "over_budget": [],
    }
    if not symbols:
        text = render_explore(res)
        res["budget"]["used"] = len(text) // 4
        return res
    entry_points, paths, blast = _collect(st, symbols)
    res["entry_points"] = entry_points
    res["paths"] = paths
    res["blast"] = blast
    res["next"] = _followups(symbols)
    _fit_fixed(res, budget)
    _fit_sources(st, res, budget)
    return res


def render_explore(res: dict) -> str:
    """Compact text for `cg explore` and the MCP tool. The last line is the token budget."""
    if not res.get("symbols"):
        stems = res.get("stems") or []
        tried = ", ".join(stems) if stems else "(none)"
        body = (f"explore: nothing matched {res.get('query')!r}; try search(<part of a name>)\n"
                f"stems tried: {tried}")
    else:
        body = _body(res)
    return body + "\n" + _budget_line(res)
