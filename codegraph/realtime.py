"""Realtime channel queries: who can join a channel, which events publish on it, which client code listens."""
from __future__ import annotations

import fnmatch
import json
from collections import defaultdict
from pathlib import Path

from .core.store import GraphStore
from .query import short_id
from .plugins.php.strings import channel_match

CHECK_KINDS = ("CALLS", "HANDLED_BY", "READS_COLUMN", "READS_TABLE", "READS_CONFIG", "READS_ENV", "READS_SETTING",
               "MENTIONS_COLUMN", "USES_CONNECTION")


def _attrs(r) -> dict:
    return json.loads(r["attrs"] or "{}") if r["attrs"] else {}


def source_path(st: GraphStore, file: str | None) -> Path | None:
    """Absolute path of a graph file (single-repo DB: meta root; combined DB: the repo's recorded root)."""
    if not file:
        return None
    m = st.meta()
    if m.get("root"):
        p = Path(m["root"]) / file
        return p if p.exists() else None
    repo, _, rel = file.partition("/")
    src = (m.get("sources") or {}).get(repo)
    if src and Path(src).exists():
        root = GraphStore(src).meta().get("root")
        if root and (Path(root) / rel).exists():
            return Path(root) / rel
    return None


def excerpt(st: GraphStore, file: str | None, lo: int | None, hi: int | None, max_lines=14) -> list[str]:
    p = source_path(st, file)
    if not p or not lo:
        return []
    try:
        lines = p.read_text(errors="replace").splitlines()
    except OSError:
        return []
    hi = min(hi or lo, lo + max_lines - 1)
    return [f"{i:5}  {lines[i - 1]}" for i in range(lo, min(hi, len(lines)) + 1)]


def _select(rows, pattern: str | None):
    if not pattern:
        return rows
    p = pattern[len("channel:"):] if pattern.startswith("channel:") else pattern
    out = []
    for r in rows:
        name = r["name"]
        if name == p or fnmatch.fnmatchcase(name, p) or (("*" not in p) and (channel_match(p, name) or channel_match(name, p))) \
                or (len(p) > 2 and "*" not in p and "{" not in p and p.lower() in name.lower()):
            out.append(r)
    return out


def channels(st: GraphStore, pattern: str | None = None, with_source: bool = True) -> dict:
    """Backend channels (declared + published-only) and client subscriptions, each with:
    auth (auth routes + middleware, callback location, checks it calls), publishers (events, dispatch sites, entry
    points), subscribers (client code / pages and the events they listen for) and flags."""
    rows = [dict(r) for r in st.q("SELECT * FROM nodes WHERE kind='channel' ORDER BY id")]
    subs_all = [dict(r) for r in st.q("SELECT * FROM nodes WHERE kind='channel_sub' ORDER BY id")]
    has_frontend = bool(subs_all)
    has_backend = bool(rows)
    out = []
    for n in _select(rows, pattern):
        a = _attrs(n)
        nid = n["id"]
        item = {"id": nid, "pattern": a.get("pattern") or n["name"], "declared": bool(a.get("declared")),
                "visibility": a.get("visibility"), "at": f"{n['file']}:{n['line']}" if n["file"] else None, "flags": []}
        # ---- who can join
        auth = {"callback": a.get("callback"), "handler": a.get("handler"), "guards": a.get("guards"), "routes": [], "checks": []}
        for r in st.q("""SELECT e.src, n.attrs, n.file, n.line FROM edges e JOIN nodes n ON n.id=e.src
                         WHERE e.dst=? AND e.kind='AUTHORIZES_CHANNEL'""", (nid,)):
            ra = _attrs(r)
            auth["routes"].append({"route": r["src"], "middleware": ra.get("middleware"), "at": f"{r['file']}:{r['line']}",
                                   "synthesized": ra.get("synthesized")})
        kq = ",".join("?" * len(CHECK_KINDS))
        srcs = [nid] + [r["dst"] for r in st.q("SELECT dst FROM edges WHERE src=? AND kind='HANDLED_BY'", (nid,))]
        for s in srcs:
            for r in st.q(f"SELECT dst, kind, file, line, confidence FROM edges WHERE src=? AND kind IN ({kq}) ORDER BY line", (s, *CHECK_KINDS)):
                auth["checks"].append({"via": s, "kind": r["kind"], "target": r["dst"], "at": f"{r['file']}:{r['line']}",
                                       "confidence": r["confidence"]})
        if with_source and n["file"]:
            auth["source"] = excerpt(st, n["file"], n["line"], n["end_line"])
            for s in srcs[1:]:
                m = st.node(s)
                if m:
                    auth["source"] += [""] + excerpt(st, m["file"], m["line"], m["end_line"])
        item["auth"] = auth
        # ---- who publishes
        pubs = []
        for r in st.q("""SELECT e.src, e.file, e.line, e.attrs, e.confidence FROM edges e WHERE e.dst=? AND e.kind='BROADCASTS_ON'
                         ORDER BY e.src, e.file, e.line""", (nid,)):
            ea = _attrs(r)
            p = {"event": r["src"], "name": ea.get("name"), "visibility": ea.get("visibility"), "at": ea.get("at"),
                 "site": ea.get("site"), "confidence": r["confidence"], "dispatched_by": []}
            q = "SELECT src, file, line FROM edges WHERE dst=? AND kind='DISPATCHES'"
            for d in st.q(q, (r["src"],)):
                if ea.get("site_fn") and d["src"] != ea["site_fn"]:
                    continue
                ents = {e["entry_kind"]: (e["entry_count"], e["sample_entry"]) for e in
                        st.q("SELECT entry_kind, entry_count, sample_entry FROM node_entry WHERE node_id=?", (d["src"],))}
                p["dispatched_by"].append({"fn": d["src"], "at": f"{d['file']}:{d['line']}",
                                           "entry_kinds": {k: v[0] for k, v in ents.items()},
                                           "sample_entry": next((v[1] for v in ents.values()), None)})
            pubs.append(p)
        item["publishers"] = pubs
        # ---- who listens (linked frontend)
        subs = []
        for r in st.q("""SELECT e.src, e.confidence, e.attrs AS eattrs, n.attrs, n.name FROM edges e JOIN nodes n ON n.id=e.src
                         WHERE e.dst=? AND e.kind='MATCHES_CHANNEL'""", (nid,)):
            sub = _subscription(st, r["src"], _attrs(r), r["confidence"])
            mm = json.loads(r["eattrs"] or "{}").get("visibility_mismatch")
            if mm:
                sub["flag"] = f"VISIBILITY MISMATCH: {mm}"
            subs.append(sub)
        item["subscribers"] = subs
        # ---- flags
        if not item["declared"] and (item["visibility"] or "private") != "public":
            item["flags"].append("UNDECLARED: published on a private/presence channel with no Broadcast::channel "
                                 "callback, so every client subscription is rejected")
        pub_public = sorted({p["event"] for p in pubs if p.get("visibility") == "public"})
        if item["declared"] and pub_public:
            item["flags"].append(f"PUBLIC PUBLISH: {len(pub_public)} event(s) publish on a public Channel with this name "
                                 f"(e.g. {short_id(pub_public[0])}); the authorization callback only guards private-/presence- "
                                 "subscriptions, so anyone can listen to these")
        if item["declared"] and not auth["routes"]:
            item["flags"].append("no broadcasting auth route found (Broadcast::routes / withBroadcasting / explicit route)")
        if not pubs:
            item["flags"].append("no indexed event publishes on this channel")
        if has_frontend and not subs:
            item["flags"].append("no client subscription matches this channel")
        out.append(item)
    orphans = []
    if has_frontend:
        linked = {r["src"] for r in st.q("SELECT src FROM edges WHERE kind='MATCHES_CHANNEL'")}
        for s in _select(subs_all, pattern) if pattern else subs_all:
            if s["id"] in linked:
                continue
            o = _subscription(st, s["id"], _attrs(s), None)
            if has_backend:
                o["flag"] = "no backend channel matches this subscription"
            orphans.append(o)
    return {"pattern": pattern, "channels": out, "client_subscriptions_unmatched": orphans,
            "has_backend": has_backend, "has_frontend": has_frontend}


def _subscription(st, sid, a, conf):
    sub = {"subscription": sid, "name": a.get("name") or sid.split(":", 1)[1], "visibility": a.get("visibility"),
           "events": a.get("events") or [], "confidence": conf, "subscribed_in": [], "pages": [], "listens_for": []}
    for r in st.q("SELECT src, file, line, confidence FROM edges WHERE dst=? AND kind='SUBSCRIBES_CHANNEL' ORDER BY file, line", (sid,)):
        sub["subscribed_in"].append({"fn": r["src"], "at": f"{r['file']}:{r['line']}", "confidence": r["confidence"]})
    for r in st.q("SELECT dst, confidence FROM edges WHERE src=? AND kind='LISTENS_FOR'", (sid,)):
        sub["listens_for"].append(r["dst"])
    pages = set()
    for s in sub["subscribed_in"]:
        for r in st.q("SELECT sample_entry, entry_kind FROM node_entry WHERE node_id=? AND entry_kind IN ('ui_page','ui_global')", (s["fn"],)):
            pages.add(r["sample_entry"])
        n = st.node(s["fn"])
        if n and n["entry_kind"] in ("ui_page", "ui_global"):
            pages.add(s["fn"])
    if sub["subscribed_in"]:
        from .query import reverse_closure
        dep = reverse_closure(st, [s["fn"] for s in sub["subscribed_in"]], max_depth=12)
        if dep:
            q = ",".join("?" * len(dep))
            for r in st.q(f"SELECT id FROM nodes WHERE id IN ({q}) AND kind IN ('page','layout','app')", list(dep)):
                pages.add(r["id"])
    sub["pages"] = sorted(pages)
    return sub


def render_channels(res: dict, max_items=40) -> str:
    from .query import short_id
    L = []
    chans = res["channels"]
    if not chans and not res["client_subscriptions_unmatched"]:
        return f"no channel matches {res['pattern']!r}" if res["pattern"] else "no broadcast channels in this graph"
    detail = len(chans) <= 3 and res["pattern"]
    if not detail:
        L.append(f"channels: {len(chans)}" + (f" matching {res['pattern']!r}" if res["pattern"] else ""))
        for c in chans[:max_items]:
            au = c["auth"]
            who = au.get("handler") or au.get("callback") or ("public: no auth" if c["visibility"] == "public" else "NO CALLBACK")
            checks = sorted({short_id(x["target"]) for x in au["checks"] if x["kind"] in ("CALLS", "HANDLED_BY")})
            L.append(f"  {c['pattern']}  [{c['visibility'] or '?'}{'' if c['declared'] else ', undeclared'}]  auth: {who}"
                     + (f"  checks: {', '.join(checks[:4])}" if checks else ""))
            L.append(f"      publishers: {len(c['publishers'])} ({', '.join(sorted({short_id(p['event']) for p in c['publishers']})[:4])})"
                     + (f"  subscribers: {len(c['subscribers'])}" if res["has_frontend"] else ""))
            for fl in c["flags"]:
                if not fl.startswith("no indexed event") and not fl.startswith("no client"):
                    L.append(f"      ! {fl}")
        if len(chans) > max_items:
            L.append(f"  ... {len(chans) - max_items} more")
    else:
        for c in chans:
            au = c["auth"]
            L.append(f"== channel {c['pattern']}  [{c['visibility'] or '?'}{'' if c['declared'] else ', UNDECLARED'}]"
                     + (f"  @ {c['at']}" if c["at"] else ""))
            L.append("WHO CAN JOIN")
            if c["visibility"] == "public":
                L.append("  public channel: anyone can subscribe (no authorization)")
            for r in au["routes"]:
                L.append(f"  auth route {short_id(r['route'])}  middleware={r['middleware']}"
                         + (f"  (from {r['synthesized']})" if r.get("synthesized") else f"  @ {r['at']}"))
            if au.get("guards"):
                L.append(f"  guards: {au['guards']}")
            if au.get("handler"):
                L.append(f"  channel class {au['handler']} (join())")
            elif au.get("callback"):
                L.append(f"  callback {au['callback']}")
            elif c["declared"] is False and c["visibility"] != "public":
                L.append("  no Broadcast::channel callback: private subscriptions are rejected")
            for x in au["checks"]:
                L.append(f"    {x['kind']} {short_id(x['target'])}  @ {x['at']} [{x['confidence']}]")
            if au.get("source"):
                L += ["  source:"] + ["  " + s for s in au["source"]]
            L.append(f"PUBLISHED BY ({len(c['publishers'])})")
            for p in c["publishers"]:
                L.append(f"  {short_id(p['event'])}  name={p['name']}  [{p['visibility']}]  broadcastOn @ {p['at']}"
                         + (f"  value from {p['site']}" if p.get("site") else ""))
                for d in p["dispatched_by"][:8]:
                    ek = ", ".join(f"{k}({v})" for k, v in sorted(d["entry_kinds"].items()))
                    L.append(f"      dispatched by {short_id(d['fn'])} @ {d['at']}  {ek}"
                             + (f"  e.g. {d['sample_entry']}" if d.get("sample_entry") else ""))
            if res["has_frontend"]:
                L.append(f"LISTENED TO BY ({len(c['subscribers'])})")
                for s in c["subscribers"]:
                    L += _render_sub(s)
            for fl in c["flags"]:
                L.append(f"! {fl}")
            L.append("")
    if res["client_subscriptions_unmatched"]:
        L.append(f"client subscriptions without a backend channel: {len(res['client_subscriptions_unmatched'])}"
                 if res["has_backend"] else f"client subscriptions: {len(res['client_subscriptions_unmatched'])}")
        for s in res["client_subscriptions_unmatched"][:max_items]:
            L += _render_sub(s)
    return "\n".join(L).rstrip()


def _render_sub(s) -> list[str]:
    from .query import short_id
    L = [f"  {s['name']}  [{s['visibility']}]" + (f"  events: {', '.join(s['events'][:6])}" if s["events"] else "")
         + (f"  ({s['confidence']})" if s.get("confidence") else "")]
    for x in s["subscribed_in"][:6]:
        L.append(f"      subscribed in {short_id(x['fn'])} @ {x['at']}")
    if s["pages"]:
        L.append(f"      pages: {', '.join(short_id(p) for p in s['pages'][:8])}" + (" ..." if len(s["pages"]) > 8 else ""))
    if s["listens_for"]:
        L.append(f"      listens for backend events: {', '.join(short_id(e) for e in s['listens_for'][:6])}")
    if s.get("flag"):
        L.append(f"      ! {s['flag']}")
    return L
