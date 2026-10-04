"""`cg protocols` / MCP `protocol_links`: every protocol endpoint of a graph in one view.

Endpoints come from the generic model (`endpoint:<protocol>:<name>` with SENDS_TO / RECEIVED_BY / MATCHES_ENDPOINT)
and from the node kinds cg emitted before it, read through adapters with their ids unchanged:

  kind          protocol                         senders (into the node)            receivers                 matches
  http          http (ws for http:WS ...)        HTTP_CALLS                         -                         MATCHES_ROUTE ->
  route         http / ws / graphql              (test: TEST_HTTP)                  ROUTES_TO                 <- MATCHES_ROUTE
  channel       pusher                           BROADCASTS_ON (events)             -                         <- MATCHES_CHANNEL
  channel_sub   pusher                           -                                  SUBSCRIBES_CHANNEL (code) MATCHES_CHANNEL ->
  message       nest-rpc / nest-event / nest-ws / grpc   DISPATCHES                 HANDLED_BY
  job           bull / laravel-queue / celery    DISPATCHES (also to the handler,   HANDLED_BY
                                                 via=job), SCHEDULES, SENDS_TO (the
                                                 endpoint:job twin's, merged; #36)
  event         laravel-event / nest-event-emitter / django-signal   DISPATCHES     LISTENED_BY / HANDLED_BY
  endpoint      attrs.protocol (bridges, MQTT, Socket.IO, ...)   SENDS_TO           RECEIVED_BY               MATCHES_ENDPOINT

Checks (per endpoint; a side is judged only when the graph holds some endpoint of that protocol on the other side, so
a single backend graph does not call every route `no_sender`):
  no_receiver      sent, nothing receives it here (directly or through a match)
  no_sender        received, nothing sends it (dead handler, or the producer is outside the analysed repos)
  test_sender_only received, sent from tests only
  ambiguous        one sender matched several receivers equally well
  schema_mismatch  senders and receivers name different message types
  no_consumer      a queue jobs are sent to; the repo starts workers (Procfile, compose, ...) and none consumes it
  unguarded        a receiver reachable from outside (http / ws / graphql routes, Socket.IO handlers, ...) with no
                   auth guard recorded (the same classification as `cg routes --unguarded`)
  external         declared external in .cg.yaml (protocols.external: ["kafka:audit.*"]), a third-party HTTP origin,
                   or a bridge module implemented outside the repo
Bridge endpoints (Capacitor, React Native, Flutter, Electron, Tauri) keep the checks codegraph/bridges.py computed.
"""
from __future__ import annotations

import fnmatch
import json
from collections import defaultdict

from . import REGISTRY, compatible, external_match

KINDS = ("http", "route", "channel", "channel_sub", "message", "job", "event", "endpoint")
EDGE_ROLE_KINDS = ("HTTP_CALLS", "MATCHES_ROUTE", "ROUTES_TO", "USES_MIDDLEWARE", "TEST_HTTP", "BROADCASTS_ON",
                   "SUBSCRIBES_CHANNEL", "MATCHES_CHANNEL", "DISPATCHES", "HANDLED_BY", "LISTENED_BY", "SCHEDULES",
                   "SENDS_TO", "RECEIVED_BY", "QUEUE_ROUTES", "MATCHES_ENDPOINT", "TEST_CALLS")
NEST = {"rpc": "nest-rpc", "event": "nest-event", "ws": "nest-ws", "grpc": "grpc"}
SEND_IN = {"http": ("HTTP_CALLS",), "route": ("SENDS_TO",), "channel": ("BROADCASTS_ON",), "message": ("DISPATCHES",),
           "job": ("DISPATCHES", "SCHEDULES", "SENDS_TO"), "event": ("DISPATCHES",), "endpoint": ("SENDS_TO",)}
RECV_OUT = {"route": ("ROUTES_TO",), "message": ("HANDLED_BY",), "job": ("HANDLED_BY",),
            "event": ("LISTENED_BY", "HANDLED_BY"), "endpoint": ("RECEIVED_BY", "QUEUE_ROUTES")}
RECV_IN = {"channel_sub": ("SUBSCRIBES_CHANNEL",)}
MATCH = ("MATCHES_ROUTE", "MATCHES_CHANNEL", "MATCHES_ENDPOINT")
TEST_ORIG = ("HTTP_CALLS", "DISPATCHES", "SENDS_TO", "SUBSCRIBES_CHANNEL")


def protocol_of(kind: str, nid: str, a: dict, lang: str | None) -> str | None:
    if kind == "endpoint":
        return a.get("protocol")
    if kind == "http":
        return "ws" if a.get("method") == "WS" else "http"
    if kind == "route":
        m = (a.get("method") or "").upper()
        return "ws" if m == "WS" else "graphql" if m == "GRAPHQL" or nid.startswith("route:GRAPHQL ") else "http"
    if kind in ("channel", "channel_sub"):
        return "pusher"
    if kind == "message":
        return NEST.get(a.get("transport") or nid.split(":")[1], "nest-" + (a.get("transport") or "message"))
    if kind == "job":
        fw = a.get("framework") or ""
        return "bull" if fw.startswith("bull") or lang == "ts" else "laravel-queue" if lang == "php" else "celery"
    if kind == "event":
        fw = a.get("framework") or ""
        if fw == "nest-event-emitter" or lang == "ts":
            return "nest-event-emitter"
        return "django-signal" if a.get("signal") or lang == "python" else "laravel-event"
    return None


def name_of(kind: str, nid: str, name: str, a: dict) -> str:
    key = nid.split(":", 1)[1]
    if kind == "channel":
        return a.get("pattern") or key
    if kind == "channel_sub":
        return a.get("name") or key
    if kind == "message":
        return key.split(":", 1)[1] if ":" in key else key
    if kind == "endpoint":
        return name or key.split(":", 1)[-1]
    return key


def _externals(st) -> list[str]:
    """protocols.external of the indexed repo(s) (.cg.yaml, recorded in the index stats)."""
    from ..routes import _meta_of
    try:
        m = st.meta()
    except Exception:  # noqa: BLE001
        return []
    metas = [_meta_of((m.get("sources") or {}).get(r)) for r in m.get("repos") or []] or [m]
    out = []
    for mm in metas:
        out += (((mm.get("stats") or {}).get("config") or {}).get("protocols") or {}).get("external") or []
    return list(dict.fromkeys(out))


def _load(st):
    nodes = {}
    q = ",".join("?" * len(KINDS))
    for r in st.q(f"SELECT id, kind, name, file, line, lang, attrs, entry_kind FROM nodes WHERE kind IN ({q})", KINDS):
        a = json.loads(r["attrs"] or "{}") if r["attrs"] else {}
        proto = protocol_of(r["kind"], r["id"], a, r["lang"])
        if not proto:
            continue
        nodes[r["id"]] = {"id": r["id"], "kind": r["kind"], "protocol": proto, "name": name_of(r["kind"], r["id"], r["name"], a),
                          "file": r["file"], "line": r["line"], "attrs": a, "entry_kind": r["entry_kind"]}
    q2 = ",".join("?" * len(EDGE_ROLE_KINDS))
    edges = [dict(r) for r in st.q(f"SELECT src, dst, kind, file, line, confidence, attrs FROM edges WHERE kind IN ({q2})",
                                   EDGE_ROLE_KINDS)]
    # GraphQL (#34): a Nest route:GRAPHQL Query.x and its endpoint:graphql:Query.x twin are one endpoint, shown as the
    # route (its guards); the twin's senders and matches move to the route, its RECEIVED_BY repeats the ROUTES_TO
    twin = {}
    for nid, n in nodes.items():
        if n["kind"] == "route" and n["protocol"] == "graphql" and n["attrs"].get("graphql") in ("Query", "Mutation", "Subscription"):
            t = f"endpoint:graphql:{n['attrs']['graphql']}.{n['attrs'].get('field')}"
            if t in nodes:
                twin[t] = nid
    if twin:
        for t, nid in twin.items():
            for k in ("served", "declared_in", "type"):
                if nodes[t]["attrs"].get(k) is not None:
                    nodes[nid]["attrs"].setdefault(k, nodes[t]["attrs"][k])
            del nodes[t]
        out = []
        for e in edges:
            if e["src"] in twin:
                if e["kind"] == "RECEIVED_BY":
                    continue
                e["src"] = twin[e["src"]]
            if e["dst"] in twin:
                e["dst"] = twin[e["dst"]]
            out.append(e)
        edges = out
    # job queues (#36): a Celery job node of the Django plugin and its endpoint:job:celery:<name> twin are one endpoint,
    # shown as the job node (its DISPATCHES / SCHEDULES); the twin's senders and matches move to it
    jt = {nid: n["attrs"]["job_node"] for nid, n in nodes.items()
          if n["kind"] == "endpoint" and n["attrs"].get("job_node") in nodes}
    if jt:
        for t, nid in jt.items():
            for k in ("queue", "processes", "task"):
                if nodes[t]["attrs"].get(k) is not None:
                    nodes[nid]["attrs"].setdefault(k, nodes[t]["attrs"][k])
            del nodes[t]
        out = []
        for e in edges:
            if e["src"] in jt:
                if e["kind"] == "RECEIVED_BY":
                    continue
                e["src"] = jt[e["src"]]
            if e["dst"] in jt:
                e["dst"] = jt[e["dst"]]
            out.append(e)
        edges = out
    return nodes, edges


def collect(st) -> dict:
    """Every endpoint with senders / receivers / matches / guards / checks (no per-endpoint queries)."""
    from ..routes import AuthMatcher, guard_setup, route_guards
    nodes, edges = _load(st)
    ep = {nid: dict(n, senders=[], test_senders=[], receivers=[], matches=[], mw=[]) for nid, n in nodes.items()}
    job_of_handler = {}
    for e in edges:
        if e["kind"] == "HANDLED_BY" and e["src"] in ep and ep[e["src"]]["kind"] == "job":
            job_of_handler.setdefault(e["dst"], e["src"])
    for e in edges:
        k, s, d = e["kind"], e["src"], e["dst"]
        a = json.loads(e["attrs"] or "{}") if e["attrs"] else {}
        at = f"{e['file']}:{e['line']}"
        if k in MATCH:
            if s in ep and d in ep:
                ep[s]["matches"].append({"endpoint": d, "confidence": e["confidence"], "dir": "out", "kind": k,
                                         "ambiguous": a.get("ambiguous")})
                ep[d]["matches"].append({"endpoint": s, "confidence": e["confidence"], "dir": "in", "kind": k})
            continue
        if k == "USES_MIDDLEWARE":
            if s in ep:
                ep[s]["mw"].append(e)
            continue
        if k in ("TEST_CALLS", "TEST_HTTP"):
            if d in ep and (k == "TEST_HTTP" or a.get("orig") in TEST_ORIG):
                ep[d]["test_senders"].append({"fn": s, "at": at, "confidence": e["confidence"]})
            continue
        if d in ep and k in SEND_IN.get(ep[d]["kind"], ()):
            ep[d]["senders"].append({"fn": s, "at": at, "confidence": e["confidence"], "via": a.get("via") or a.get("role"),
                                     **({k2: a[k2] for k2 in ("schema", "process") if a.get(k2)})})
        elif k == "DISPATCHES" and a.get("via") == "job" and d in job_of_handler:
            ep[job_of_handler[d]]["senders"].append({"fn": s, "at": at, "confidence": e["confidence"], "via": "dispatch"})
        if s in ep and k in RECV_OUT.get(ep[s]["kind"], ()):
            ep[s]["receivers"].append({"handler": d, "at": at, "confidence": e["confidence"],
                                       **({k2: a[k2] for k2 in ("platform", "process", "schema", "bind_address", "exposure")
                                          if a.get(k2) is not None and a.get(k2) != ""})})
        if d in ep and k in RECV_IN.get(ep[d]["kind"], ()):
            ep[d]["receivers"].append({"handler": s, "at": at, "confidence": e["confidence"]})
    # sides per protocol
    psend, precv = defaultdict(bool), defaultdict(bool)
    for n in ep.values():
        psend[n["protocol"]] |= bool(n["senders"])
        # a GraphQL root field the schema declares is served (default resolver, a resolver cg does not see; #34)
        n["served"] = n["kind"] == "endpoint" and bool(n["attrs"].get("served"))
        precv[n["protocol"]] |= bool(n["receivers"]) or n["served"]
    gs = guard_setup(st)
    is_auth = AuthMatcher(None, gs["applied"], gs["auth_patterns"], gs["secret_patterns"])
    ext = _externals(st)
    for n in ep.values():
        a, p = n["attrs"], REGISTRY.get(n["protocol"])
        peers = [ep[m["endpoint"]] for m in n["matches"]]
        # a directional protocol (Socket.IO): this endpoint's own senders and receivers pair up only across processes
        own = compatible(p, {x["process"] for x in n["senders"] if x.get("process")},
                         {x["process"] for x in n["receivers"] if x.get("process")})
        psent = any(x["senders"] for x in peers)
        precv_ = any(x["receivers"] or x["kind"] == "route" for x in peers)   # a matched route receives
        precv_ = precv_ or n["served"]
        if own:
            sent = bool(n["senders"]) or psent
            recv = bool(n["receivers"]) or precv_
        else:                      # e.g. a client emits `x` and a client handles `x`: neither reaches the other
            sent = psent or (bool(n["senders"]) and not n["receivers"])
            recv = precv_ or (bool(n["receivers"]) and not n["senders"])
        n["side"] = "both" if n["senders"] and n["receivers"] else "send" if n["senders"] or (n["kind"] == "http") \
            else "receive" if n["receivers"] or n["kind"] in ("route",) or n["served"] else ("send" if n["test_senders"] else "none")
        n["linked"] = sent and recv
        if not own:
            n["linked"] = bool((n["senders"] and precv_) or (n["receivers"] and psent))
        ck = []
        n["external"] = None
        if p and p.source == "bridges":
            ck = list(a.get("checks") or [])
            n["external"] = a.get("package") or ("implemented outside this repo" if a.get("external") else None)
        else:
            hit = external_match(ext, n["protocol"], n["name"])
            if hit:
                n["external"] = f".cg.yaml protocols.external {hit}"
            elif p and not sent and any(fnmatch.fnmatchcase(n["name"], g) for g in p.framework_senders):
                n["external"] = "sent by the framework"
            elif n["kind"] == "http" and a.get("origin_kind") == "other":
                n["external"] = f"other origin {a.get('origin')}"
            test_only = bool(a.get("test_only"))
            skip_route = n["kind"] == "route" and (a.get("mounted", True) is False or a.get("framework") == "django-admin")
            if (sent and not recv or not own and n["senders"] and not precv_) and precv[n["protocol"]] \
                    and not n["external"] and not test_only:
                ck.append("no_receiver")
            if (recv and not sent or not own and n["receivers"] and not psent) and psend[n["protocol"]] \
                    and not n["external"] and not skip_route and not (n["served"] and not n["receivers"]):
                ck.append("test_sender_only" if n["test_senders"] or any(x["test_senders"] for x in peers) else "no_sender")
            if n["protocol"] == "queue" and a.get("workers_known") and not a.get("consumers") and n["senders"]:
                ck.append("no_consumer")       # produced; the repo starts workers, none of them consumes this queue (#36)
            outs = [m for m in n["matches"] if m["dir"] == "out"]
            amb = any(m.get("ambiguous") for m in outs)
            if not amb and len(outs) > 1:
                if n["kind"] == "http":
                    amb = True                 # several routes equally specific (cg link keeps the ties, heuristic)
                elif n["kind"] == "channel_sub":
                    amb = any(m["confidence"] == "heuristic" for m in outs)
                elif n["kind"] == "endpoint":
                    amb = not (p and p.fanout)
            if amb:
                ck.append("ambiguous")
            ss = {x["schema"] for x in n["senders"] + [y for q in peers for y in q["senders"]] if x.get("schema")}
            rs = {x["schema"] for x in n["receivers"] + [y for q in peers for y in q["receivers"]] if x.get("schema")}
            if a.get("schema"):
                (rs if n["receivers"] else ss).add(a["schema"])
            if ss and rs and not (ss & rs):
                ck.append("schema_mismatch")
                n["schemas"] = {"sent": sorted(ss), "received": sorted(rs)}
        # guards: routes as in `cg routes`, generic receivers from attrs.guards
        if n["kind"] == "route" or (n["kind"] in ("endpoint", "message") and "guards" in a):
            if n["kind"] == "route":
                g = route_guards(a, n["mw"], is_auth)
            elif n["kind"] == "message":   # Nest @UseGuards / APP_GUARD class names: classified like route guards (#69)
                g = [{"name": x, "auth": is_auth(x), "secret": is_auth.secret(x)} for x in a.get("guards") or []]
            else:   # recorded by the plugin as a check that rejects (connect handler, io.use, interceptor): auth
                g = [{"name": x if isinstance(x, str) else x.get("name"), "auth": True} for x in a.get("guards") or []]
            n["guards"] = [x["name"] for x in g]
            has_auth = any(x["auth"] for x in g)
            secret = any(x.get("secret") for x in g)
            if (p and p.guards) and (n["receivers"] or n["kind"] == "route") and not has_auth and not secret and not n["external"] \
                    and not (n["kind"] == "route" and (a.get("mounted", True) is False or a.get("framework") == "django-admin")):
                ck.append("unguarded")
        else:
            n["guards"] = None
        n["checks"] = ck
    return ep


JOB_ADAPTERS = ("celery", "bull", "laravel-queue")   # `--protocol job` also lists the job nodes of the plugins
SOCKET_ATTRS = ("port", "port_envs", "bind_addresses", "exposure", "multicast_group")


def protocols(st, pattern: str | None = None, protocol: str | None = None, side: str | None = None,
              unmatched: bool = False, max_items: int = 200, listeners: bool = False) -> dict:
    """listeners: every listening TCP / UDP socket (#39) with its bind address, exposure and handler."""
    ep = collect(st)
    if listeners:
        side = "receive"
    summ = defaultdict(lambda: defaultdict(int))
    for n in ep.values():
        s = summ[n["protocol"]]
        s["endpoints"] += 1
        s["send"] += n["side"] in ("send", "both")
        s["receive"] += n["side"] in ("receive", "both")
        s["linked"] += n["linked"]
        for c in n["checks"]:
            s[c] += 1
        s["external"] += bool(n["external"])
    sel = []
    listing = bool(pattern or protocol or side or unmatched or listeners)
    if listing:
        p = pattern[len("endpoint:"):] if pattern and pattern.startswith("endpoint:") else pattern
        for nid in sorted(ep):
            n = ep[nid]
            if protocol and n["protocol"] != protocol and not (protocol == "job" and n["protocol"] in JOB_ADAPTERS):
                continue
            if listeners and (n["protocol"] not in ("tcp", "udp") or not n["receivers"]):
                continue
            if side and n["side"] not in (side, "both"):
                continue
            if unmatched and not (n["checks"] or n["external"]):
                continue
            if p and not (n["id"] == p or fnmatch.fnmatchcase(n["name"], p) or fnmatch.fnmatchcase(n["id"].split(":", 1)[1], p)
                          or fnmatch.fnmatchcase(f"{n['protocol']}:{n['name']}", p) or p.lower() in n["name"].lower()):
                continue
            sel.append(n)
    detail = 0 < len(sel) <= 6
    out = []
    for n in sel[:max_items]:
        item = {k: n[k] for k in ("id", "kind", "protocol", "name", "side", "linked", "checks", "external", "guards", "senders",
                                  "test_senders", "receivers", "matches")}
        item["at"] = f"{n['file']}:{n['line']}" if n["file"] else None
        item["transport"] = (REGISTRY.get(n["protocol"]).transport if REGISTRY.get(n["protocol"]) else None)
        if n.get("schemas"):
            item["schemas"] = n["schemas"]
        if n["attrs"].get("test_only"):
            item["test_only"] = True
        for k in SOCKET_ATTRS if n["protocol"] in ("tcp", "udp") else ():
            if n["attrs"].get(k) not in (None, [], ""):
                item[k] = n["attrs"][k]
        if detail:
            for s in item["senders"]:
                s["entry_kinds"] = {e["entry_kind"]: e["entry_count"] for e in
                                    st.q("SELECT entry_kind, entry_count FROM node_entry WHERE node_id=?", (s["fn"],))}
        out.append(item)
    reg = [{"name": p.name, "transport": p.transport, "matcher": getattr(p.matcher, "__name__", "custom"), "fanout": p.fanout,
            "source": p.source, "description": p.description} for p in REGISTRY.values()]
    return {"pattern": pattern, "protocol": protocol, "side": side, "unmatched": unmatched, "listeners": listeners,
            "summary": {k: dict(v) for k, v in sorted(summ.items())}, "endpoints": out, "selected": len(sel),
            "listing": listing, "registry": reg}


def render_protocols(res: dict, max_items: int = 60) -> str:
    from ..query import short_id
    L = []
    if not res["summary"]:
        return "no protocol endpoints in this graph (HTTP calls / routes, channels, messages, jobs, events, bridges)"
    if not res["listing"]:
        L.append("protocol            endpoints  send  receive  linked  checks")
        for p, s in res["summary"].items():
            ck = ", ".join(f"{k} {v}" for k, v in s.items() if k not in ("endpoints", "send", "receive", "linked") and v)
            L.append(f"{p:<19} {s['endpoints']:>9}  {s.get('send', 0):>4}  {s.get('receive', 0):>7}  {s.get('linked', 0):>6}  {ck or '-'}")
        L.append("(cg protocols --protocol P | PATTERN | --unmatched for endpoints; cg link / channels / bridges for the "
                 "HTTP, Pusher and bridge views)")
        return "\n".join(L)
    eps = res["endpoints"]
    if not eps:
        return "no protocol endpoint matches " + ", ".join(
            f"{k}={res[k]!r}" for k in ("pattern", "protocol", "side") if res.get(k)) + (" (unmatched only)" if res["unmatched"] else "")
    if res.get("listeners"):
        L.append("listening sockets (port, bind address / exposure, handler):")
        for i in eps[:max_items]:
            extra = (f"  group {i['multicast_group']}" if i.get("multicast_group") else "") + \
                (f"  env {', '.join(i['port_envs'])}" if i.get("port_envs") else "")
            for r in i["receivers"][:6]:
                b = r.get("bind_address", "*" if r.get("exposure") == "all" else "?") or "*"
                L.append(f"[{i['protocol']}] {i['name']:<8} {r.get('exposure') or '?':<9} ({b}) -> {short_id(r['handler'])} @ {r['at']}"
                         f" [{r['confidence']}]{extra}")
        if res["selected"] > min(len(eps), max_items):
            L.append(f"... {res['selected'] - min(len(eps), max_items)} more")
        return "\n".join(L)
    detail = len(eps) <= 6
    for i in eps[:max_items]:
        flags = list(i["checks"]) + ([f"external ({i['external']})"] if i["external"] else [])
        L.append(f"[{i['protocol']}] {i['name']}  ({i['side']}{', linked' if i['linked'] else ''})"
                 + (f"  guards: {', '.join(i['guards']) or 'none'}" if i["guards"] is not None else "")
                 + (f"  ! {'; '.join(flags)}" if flags else ""))
        if detail:
            L.append(f"    node {i['id']}" + (f" @ {i['at']}" if i.get("at") else ""))
            if i.get("bind_addresses") or i.get("multicast_group") or i.get("port_envs"):
                L.append("    " + "; ".join(x for x in (
                    f"bind {', '.join(b or '*' for b in i.get('bind_addresses') or [])} ({i.get('exposure')})" if i.get("bind_addresses") else "",
                    f"multicast group {i['multicast_group']}" if i.get("multicast_group") else "",
                    f"port from env {', '.join(i['port_envs'])}" if i.get("port_envs") else "") if x))
            for s in i["senders"][:8]:
                ek = ", ".join(f"{k}({v})" for k, v in sorted((s.get("entry_kinds") or {}).items()))
                L.append(f"    sent by {short_id(s['fn'])} @ {s['at']} [{s['confidence']}]" + (f"  entries: {ek}" if ek else ""))
            for s in i["test_senders"][:4]:
                L.append(f"    test {short_id(s['fn'])} @ {s['at']}")
            for r in i["receivers"][:8]:
                L.append(f"    received by {short_id(r['handler'])} @ {r['at']} [{r['confidence']}]")
            for m in i["matches"][:8]:
                L.append(f"    {'matches' if m['dir'] == 'out' else 'matched by'} {m['endpoint']} [{m['confidence']}]")
            if i.get("schemas"):
                L.append(f"    schemas: sent {i['schemas']['sent']} / received {i['schemas']['received']}")
        else:
            L.append(f"    senders {len(i['senders'])}" + (f" (+{len(i['test_senders'])} test)" if i["test_senders"] else "")
                     + f", receivers {len(i['receivers'])}, matches {len(i['matches'])}")
    if res["selected"] > min(len(eps), max_items):
        L.append(f"... {res['selected'] - min(len(eps), max_items)} more")
    return "\n".join(L)
