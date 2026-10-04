"""Protocol links (#31, epic #29): one model for every way two pieces of code talk through a named endpoint.

    sender code  -SENDS_TO->  endpoint:<protocol>:<name>  -RECEIVED_BY->  handler
                              endpoint (send side)  -MATCHES_ENDPOINT->  endpoint (receive side)   names differ but match

* Registry: one `Protocol` per protocol (name, transport, default ports, URL schemes, name normaliser, matcher,
  fan-out, whether receivers are entry points and whether guards are recorded). Plugins register theirs with
  `register(Protocol(...))`; the shared matchers live in protocols/matchers.py (path, MQTT / NATS / AMQP topics, glob,
  exact, `{param}` templates).
* Builder helpers: `protocol_send(builder, protocol, name, src, ...)` / `protocol_receive(builder, protocol, name,
  handler, ...)`: a plugin reports facts, the name is normalised here and matching happens once, at the end of
  `cg index` (`apply`) and over the combined graph of `cg link` (`link_db`). Identical ids need no edge: the combined
  DB keeps one node per id.
* Existing node kinds keep their ids and are adapted by the query (protocols/view.py): `http` / `route` (protocol
  http; `route:WS ...` ws, `route:GRAPHQL ...` graphql), `channel_sub` / `channel` (pusher), Nest `message:`
  (nest-rpc / nest-event / nest-ws / grpc), `job:` (bull, laravel-queue, celery), `event:` (laravel-event,
  nest-event-emitter, django-signal) and the bridge endpoints of codegraph/bridges.py (capacitor, react-native,
  flutter, electron-ipc, tauri, ...).
* `cg protocols` / MCP `protocol_links`: summary per protocol and one block per endpoint with senders, receivers,
  guards, matches and checks (no_receiver, no_sender, ambiguous, schema_mismatch, unguarded, test_sender_only).
"""
from __future__ import annotations

import fnmatch
import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Callable

from . import matchers as M


@dataclass
class Protocol:
    name: str
    transport: str                       # tcp | udp | quic | local (in-process / same machine) | ipc
    description: str
    matcher: Callable[[str, str], dict | None] = M.exact
    normalise: Callable[[str], str] | None = None
    ports: tuple = ()
    schemes: tuple = ()
    fanout: bool = False                 # pub/sub: every matching receiver gets the message (never ambiguous)
    entry: bool = True                   # receivers can be reached from outside the process (message_handler entry)
    guards: bool = False                 # receivers record guards: `unguarded` is checked
    source: str = "endpoint"             # endpoint (protocol_send / protocol_receive) | the adapter of an existing kind
    kinds: tuple = ("endpoint",)         # node kinds holding its endpoints
    aliases: tuple = field(default_factory=tuple)
    entry_kind: str = "message_handler"  # entry kind of a receiving endpoint (llm_tool: called by a model / MCP client)
    framework_senders: tuple = ()        # name globs the framework itself sends (Django's post_save, ...): never no_sender
    directional: bool = False            # both processes send and receive on one name (Socket.IO): a sender's
                                         # `process` (server / client) reaches only receivers of the other one (#69)


REGISTRY: dict[str, Protocol] = {}


def register(p: Protocol) -> Protocol:
    REGISTRY[p.name] = p
    return p


def get(name: str) -> Protocol | None:
    return REGISTRY.get(name)


_TEMPLATE = re.compile(r"\$\{\s*([A-Za-z_$][\w$.]*)?[^{}]*\}|(?<![\w}])\{\{\s*(\w*)[^{}]*\}\}|(?<=[/.:])\:([A-Za-z_]\w*)")


def normalise(name: str) -> str:
    """Default name normaliser: template parts become `{param}` (`${id}` / `{{id}}` -> `{id}`, `/:id` -> `/{id}`);
    surrounding blanks dropped."""
    def rep(m):
        n = m.group(1) or m.group(2) or m.group(3) or ""
        return "{" + n.rsplit(".", 1)[-1] + "}"
    return _TEMPLATE.sub(rep, (name or "").strip())


def norm_name(protocol: str, name: str) -> str:
    p = REGISTRY.get(protocol)
    return (p.normalise if p and p.normalise else normalise)(name)


def is_pattern(name: str) -> bool:
    return bool(re.search(r"\{[^{}]*\}|[*?#>+]", name or ""))


# ------------------------------------------------------------------ builder helpers
def _endpoint(builder, protocol: str, name: str, node_attrs: dict | None) -> str:
    p = REGISTRY.get(protocol)
    key = f"{protocol}:{name}"
    nid = builder.add_node("endpoint", key, name, fqn=name,
                           attrs={"protocol": protocol, "transport": p.transport if p else "tcp"})
    n = builder.nodes[nid]
    if is_pattern(name):
        n.attrs["pattern"] = name
    for k, v in (node_attrs or {}).items():
        if v not in (None, [], ""):
            n.attrs[k] = v
    return nid


def protocol_send(builder, protocol: str, name: str, src: str, file: str | None, line: int | None,
                  confidence: str, test: bool = False, role: str = "send", node_attrs: dict | None = None,
                  **attrs) -> str:
    """Code `src` sends to `endpoint:<protocol>:<name>` (SENDS_TO; from test code: TEST_CALLS with orig SENDS_TO).
    role: send | publish | emit | request | invoke | enqueue; attrs: library, payload, schema, room, ..."""
    nid = _endpoint(builder, protocol, norm_name(protocol, name), node_attrs)
    n = builder.nodes[nid]
    if test:
        n.attrs.setdefault("test_only", True)
    else:
        n.attrs["test_only"] = False
    a = {k: v for k, v in attrs.items() if v not in (None, [], "")}
    if test:
        builder.add_edge(src, nid, "TEST_CALLS", file=file, line=line, confidence=confidence, orig="SENDS_TO", role=role, **a)
    else:
        builder.add_edge(src, nid, "SENDS_TO", file=file, line=line, confidence=confidence, role=role, **a)
    return nid


def protocol_receive(builder, protocol: str, name: str, handler: str, file: str | None, line: int | None,
                     confidence: str, guards: list | None = None, node_attrs: dict | None = None, **attrs) -> str:
    """`endpoint:<protocol>:<name>` is handled by `handler` (RECEIVED_BY). guards: names of the guards / middleware /
    signature checks protecting the receiver ([] = known to have none); the endpoint becomes a message_handler entry
    point when the protocol's senders can sit outside the process."""
    nid = _endpoint(builder, protocol, norm_name(protocol, name), node_attrs)
    n = builder.nodes[nid]
    p = REGISTRY.get(protocol)
    if guards is not None:
        n.attrs["guards"] = sorted(set(n.attrs.get("guards") or []) | set(guards))
    if (p is None or p.entry) and not n.entry_kind:
        n.entry_kind = p.entry_kind if p else "message_handler"
    builder.add_edge(nid, handler, "RECEIVED_BY", file=file, line=line, confidence=confidence,
                     **{k: v for k, v in attrs.items() if v not in (None, [], "")})
    return nid


# ------------------------------------------------------------------ matching (cg index and cg link)
def compatible(p, sprocs, rprocs) -> bool:
    """A sender process set can reach a receiver process set: always, unless the protocol is directional and both
    sides name their processes (server -> client, client -> server)."""
    if p is None or not p.directional or not sprocs or not rprocs:
        return True
    return any(a != b for a in sprocs for b in rprocs)


def match_rows(eps: dict[str, tuple[str, str]], senders: set, receivers: set, existing: set = frozenset(),
               externals: list | tuple = (), procs: dict | None = None) -> tuple[list, dict]:
    """eps: endpoint id -> (protocol, name) of the generic endpoints in a graph; senders / receivers: ids with a
    SENDS_TO (or test send) / RECEIVED_BY. Returns ([(src, dst, confidence, attrs)], stats per protocol) for
    MATCHES_ENDPOINT send-side -> receive-side endpoints whose names differ but match by the protocol's matcher.
    Fan-out protocols link every matching receiver; the others the most specific ones (ties: ambiguous, heuristic)."""
    by_proto: dict[str, dict] = {}
    for nid, (proto, name) in eps.items():
        d = by_proto.setdefault(proto, {"send": [], "recv": []})
        if nid in senders:
            d["send"].append((nid, name))
        if nid in receivers:
            d["recv"].append((nid, name))
    rows, stats = [], {}
    for proto, d in sorted(by_proto.items()):
        p = REGISTRY.get(proto)
        if p is None or p.source != "endpoint":
            continue
        s = stats.setdefault(proto, {"endpoints": 0, "send": 0, "receive": 0, "matched": 0, "match_edges": 0,
                                     "no_receiver": 0, "no_sender": 0, "ambiguous": 0, "external": 0})
        ids = {nid for nid, (pp, _) in eps.items() if pp == proto}
        s["endpoints"] = len(ids)
        s["send"], s["receive"] = len(d["send"]), len(d["recv"])
        got_recv = set()
        pr = procs or {}

        def ok(a, b):
            return compatible(p, pr.get(("send", a)), pr.get(("recv", b)))
        for sid, sname in d["send"]:
            hits = []
            for rid, rname in d["recv"]:
                if rid == sid or p.matcher is M.exact or not ok(sid, rid):
                    continue
                info = p.matcher(sname, rname)
                if info is not None:
                    hits.append((rid, rname, info))
            direct = sid in receivers and ok(sid, sid)
            if direct:
                got_recv.add(sid)
            if hits and not p.fanout:
                if direct:
                    hits = []              # the exact name is received: the most specific receiver
                else:
                    top = max(M.rank(h[2]) for h in hits)
                    hits = [h for h in hits if M.rank(h[2]) == top]
            tied = len(hits) > 1 and not p.fanout
            for rid, rname, info in hits:
                got_recv.add(rid)
                if (sid, rid) in existing:
                    continue
                a = {"sender_name": sname, "pattern": rname, "segments": info}
                if tied:
                    a["ambiguous"] = len(hits)
                rows.append((sid, rid, M.confidence(info, tied), a))
                s["match_edges"] += 1
            if hits or direct:
                s["matched"] += 1
            elif externals and external_match(externals, proto, sname):
                s["external"] += 1        # .cg.yaml protocols.external: received outside these repos (#69)
            else:
                s["no_receiver"] += 1 if d["recv"] else 0
            if tied:
                s["ambiguous"] += 1
        unsent = [rname for rid, rname in d["recv"] if (rid not in senders or not ok(rid, rid)) and rid not in got_recv] \
            if d["send"] else []
        ext = [r for r in unsent if externals and external_match(externals, proto, r)]
        s["no_sender"] = len(unsent) - len(ext)
        s["external"] += len(ext)
    return rows, stats


def apply(builder, externals: list | tuple = ()) -> dict:
    """End of `cg index`: MATCHES_ENDPOINT between the generic endpoints of one graph; stats per protocol (only
    when the graph has endpoints of registered non-bridge protocols)."""
    eps = {nid: (n.attrs.get("protocol"), n.name) for nid, n in builder.nodes.items()
           if n.kind == "endpoint" and (REGISTRY.get(n.attrs.get("protocol")) or Protocol("", "", "", source="?")).source == "endpoint"}
    if not eps:
        return {}
    senders, receivers, loc, procs = set(), set(), {}, defaultdict(set)
    for e in builder.edges.values():
        if e.kind == "SENDS_TO" and e.dst in eps:
            senders.add(e.dst)
            if e.attrs.get("process"):
                procs[("send", e.dst)].add(e.attrs["process"])
        elif e.kind == "TEST_CALLS" and e.dst in eps and e.attrs.get("orig") == "SENDS_TO":
            senders.add(e.dst)
        elif e.kind == "RECEIVED_BY" and e.src in eps:
            receivers.add(e.src)
            if e.attrs.get("process"):
                procs[("recv", e.src)].add(e.attrs["process"])
            loc.setdefault(e.src, (e.file, e.line))      # the match is evidenced where the receiver registers
    receivers |= {nid for nid in eps if builder.nodes[nid].attrs.get("served")}   # GraphQL schema fields (#34)
    for nid in eps:
        a = builder.nodes[nid].attrs
        a["side"] = "both" if nid in senders and nid in receivers else "send" if nid in senders else "receive" if nid in receivers else "none"
    rows, st = match_rows(eps, senders, receivers, externals=externals, procs=procs)
    for s, d, conf, a in rows:
        f, ln = loc.get(d, (None, None))
        builder.add_edge(s, d, "MATCHES_ENDPOINT", file=f, line=ln, confidence=conf, **a)
    return st


def link_db(db) -> dict:
    """`cg link`: MATCHES_ENDPOINT over the combined graph (senders in one repo, receivers in the other)."""
    import json
    from ..core.model import CONFIDENCE_RANK
    names = [p.name for p in REGISTRY.values() if p.source == "endpoint"]
    if not names:
        return {}
    q = ",".join("?" * len(names))
    eps = {r[0]: (r[1], r[2]) for r in db.execute(
        f"SELECT id, json_extract(attrs,'$.protocol'), name FROM nodes WHERE kind='endpoint' AND json_extract(attrs,'$.protocol') IN ({q})", names)}
    if not eps:
        return {}
    senders = {r[0] for r in db.execute("SELECT DISTINCT dst FROM edges WHERE kind='SENDS_TO' OR (kind='TEST_CALLS' AND "
                                        "json_extract(attrs,'$.orig')='SENDS_TO')") if r[0] in eps}
    receivers = {r[0] for r in db.execute("SELECT DISTINCT src FROM edges WHERE kind='RECEIVED_BY'") if r[0] in eps}
    receivers |= {r[0] for r in db.execute("SELECT id FROM nodes WHERE kind='endpoint' AND "
                                           "json_extract(attrs,'$.served') IS NOT NULL") if r[0] in eps}
    existing = {(r[0], r[1]) for r in db.execute("SELECT src, dst FROM edges WHERE kind='MATCHES_ENDPOINT'")}
    externals = []
    for alias in ("b", "f"):          # protocols.external of both repos (.cg.yaml, in their index stats)
        try:
            r = db.execute(f"SELECT value FROM {alias}.meta WHERE key='stats'").fetchone()
        except Exception:  # noqa: BLE001  (not attached: a graph linked on its own)
            r = None
        if r:
            externals += ((json.loads(r[0]).get("config") or {}).get("protocols") or {}).get("external") or []
    procs = defaultdict(set)
    for kind, col, side in (("SENDS_TO", "dst", "send"), ("RECEIVED_BY", "src", "recv")):
        for nid, pr in db.execute(f"SELECT {col}, json_extract(attrs,'$.process') FROM edges WHERE kind=? AND "
                                  "json_extract(attrs,'$.process') IS NOT NULL", (kind,)):
            if nid in eps:
                procs[(side, nid)].add(pr)
    rows, st = match_rows(eps, senders, receivers, existing, externals=list(dict.fromkeys(externals)), procs=procs)
    loc = {}
    for r in db.execute("SELECT src, file, line FROM edges WHERE kind='RECEIVED_BY' ORDER BY rowid"):
        loc.setdefault(r[0], (r[1], r[2]))
    db.executemany("INSERT INTO edges(src,dst,kind,file,line,confidence,conf_rank,attrs,gate) VALUES (?,?,?,?,?,?,?,?,?)",
                   [(s, d, "MATCHES_ENDPOINT", *loc.get(d, (None, None)), c, CONFIDENCE_RANK[c], json.dumps(a), None)
                    for s, d, c, a in rows])
    db.commit()
    return st


def external_match(patterns: list[str], protocol: str, name: str) -> str | None:
    """`.cg.yaml` protocols.external entries (`kafka:audit.*`, `http:GET /v1/*`, `socketio:*`): the first one
    matching `<protocol>:<name>` (glob)."""
    for p in patterns or []:
        pp, _, nn = p.partition(":")
        if fnmatch.fnmatchcase(protocol, pp) and fnmatch.fnmatchcase(name, nn or "*"):
            return p
    return None


from . import builtin  # noqa: E402,F401  (registers the built-in protocols)
