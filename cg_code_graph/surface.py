"""Security views over facts cg already records (#47 part 1): `cg surface` / MCP `attack_surface`.

Two surfaces and a set of findings, all read from the graph (no source scan):

  inbound    what the code exposes: HTTP routes with their guard state, webhooks (verified or not), WebSocket /
             Socket.IO / SSE, gRPC / GraphQL / tRPC / JSON-RPC handlers, message consumers, raw TCP / UDP listeners with
             their bind exposure, Unix sockets and the IPC handlers cg models (Electron / Tauri / Android), each with its
             guard / auth state and whether it reaches a write (`cg routes --writes`)
  outbound   every `external:` system with its address source, credential source and TLS state (`cg external`)

  findings   hardcoded         a literal credential (external node / edge, DSN with a password, config literal)
             plaintext         an external system reached without TLS (non-loopback host)
             unverified        a webhook route that reads a provider header and never verifies it
             unguarded         an inbound route / handler with no guard (`cg routes --unguarded`; `strict`: route guards only)
             exposed-listener  a TCP / UDP listener bound to all interfaces

One finding shape: {finding, severity, confidence, protocol, node, file, line, entry_points, detail, fingerprint}. The
fingerprint is stable across runs (finding + node id + file, no line number) and is what `surface.ignore` entries in
.cg.yaml and SARIF `partialFingerprints` use. A secret value is never stored or printed: a finding names the kind and
the location. Each finding type is produced by one function registered with `@producer("<finding>")` over a shared
`Ctx`; a new detector adds a `FINDINGS` row and a producer, nothing else.
"""
from __future__ import annotations

import fnmatch
import hashlib
import json
import re
from collections import defaultdict
from pathlib import Path

from .core.model import CONFIDENCE_RANK, DEV_ENTRY_KINDS, PROPAGATING
from .core.store import GraphStore

SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2}
SARIF_SCHEMA = "https://docs.oasis-open.org/sarif/sarif/v2.1.0/errata01/os/schemas/sarif-schema-2.1.0.json"
ENTRY_CAP = 5

# finding -> rule metadata; `direction` says which surface it belongs to (--inbound / --outbound)
FINDINGS: dict[str, dict] = {
    "hardcoded": {"severity": "high", "direction": "outbound", "title": "Hardcoded credential",
                  "help": "A password, key or token is a literal in code or configuration. Read it from the environment "
                          "or a secret store and rotate the exposed value."},
    "plaintext": {"severity": "medium", "direction": "outbound", "title": "Plaintext protocol",
                  "help": "An external system is reached without TLS. Use the TLS variant of the protocol "
                          "(https, rediss, amqps, ldaps, ...)."},
    "unverified": {"severity": "high", "direction": "inbound", "title": "Webhook without signature verification",
                   "help": "The webhook handler reads a provider header or payload but never verifies the signature "
                           "or shared secret, so anyone can post events to it."},
    "unguarded": {"severity": "medium", "direction": "inbound", "title": "Inbound handler without a guard",
                  "help": "A route or handler reachable from outside has no auth guard recorded. Add one, or ignore it "
                          "with a reason in .cg.yaml surface.ignore when it is public by design."},
    "exposed-listener": {"severity": "medium", "direction": "inbound", "title": "Listener bound to all interfaces",
                         "help": "A TCP / UDP listener binds 0.0.0.0 / ::. Bind a loopback or specific address "
                                 "unless it is meant to be reachable from the network."},
}
_PRODUCERS: dict[str, callable] = {}
LOOPBACK = {"localhost", "127.0.0.1", "::1", "0.0.0.0", "host.docker.internal", ""}
_LOC = re.compile(r"^(?P<file>[^:\s][^:]*?):(?P<line>\d+)$")
DEV_KINDS = set(DEV_ENTRY_KINDS)
# endpoints that are not an inbound surface of their own: the webhook endpoint twin of a route (the route carries the
# guard / verification state), job queue routing nodes (their tasks are the `job` consumers), in-process messaging
NOT_INBOUND = {"webhook", "queue", "worker", "broadcastchannel"}


def producer(name: str):
    def deco(fn):
        _PRODUCERS[name] = fn
        return fn
    return deco


def finding_names() -> list[str]:
    return list(FINDINGS)


# ------------------------------------------------------------------ shared context
class Ctx:
    """What the producers share: the store, repo layout of a combined graph, lazy caches."""

    def __init__(self, st: GraphStore):
        self.st = st
        meta = st.meta()
        self.repos: list[str] = list(meta.get("repos") or [])
        self.combined = bool(self.repos)
        self._cache: dict = {}

    def cached(self, key, fn):
        if key not in self._cache:
            self._cache[key] = fn()
        return self._cache[key]

    @property
    def externals(self) -> list[dict]:
        def load():
            rows = []
            for r in self.st.q("SELECT id, name, file, line, attrs FROM nodes WHERE kind='external' ORDER BY id"):
                rows.append({"id": r["id"], "attrs": json.loads(r["attrs"] or "{}")})
            return rows
        return self.cached("externals", load)

    def conn_edges(self, nid: str) -> list[dict]:
        idx = self.cached("conn_edges", lambda: _group(
            self.st.q("SELECT src, dst, file, line, confidence, attrs FROM edges WHERE kind='CONNECTS_TO'"), "dst"))
        return idx.get(nid, [])

    def repo_of(self, attrs: dict | None, file: str | None) -> str | None:
        if not self.combined:
            return None
        if file and "/" in file:
            head = file.split("/", 1)[0]
            if head in self.repos:
                return head
        return (attrs or {}).get("repo")

    def full_file(self, repo: str | None, file: str | None) -> str | None:
        """File as stored in a combined graph (`<repo>/<path>`); a single-repo graph keeps repo-relative paths."""
        if not file or not repo or file.startswith(repo + "/"):
            return file
        return f"{repo}/{file}"

    def rel_file(self, repo: str | None, file: str | None) -> str | None:
        if file and repo and file.startswith(repo + "/"):
            return file[len(repo) + 1:]
        return file

    def node_loc(self, nid: str) -> tuple[str | None, int | None]:
        r = self.st.node(nid)
        return (r["file"], r["line"]) if r else (None, None)

    def entry_points(self, ids, own: str | None = None) -> tuple[list[dict], int]:
        """Entry kinds reaching `ids` (summed over them), one {kind, count, sample} per kind, capped; and the total."""
        from . import query as Q
        ids = [i for i in dict.fromkeys(ids) if i]
        kinds: dict[str, list] = {}
        for _nid, per in Q.entry_info(self.st, ids).items():
            for kind, (cnt, sample) in per.items():
                if kind in DEV_KINDS:
                    continue
                cur = kinds.setdefault(kind, [0, sample])
                cur[0] += cnt
        out = [{"kind": k, "count": v[0], "sample": v[1]} for k, v in sorted(kinds.items(), key=lambda kv: (-kv[1][0], kv[0]))]
        total = sum(e["count"] for e in out)
        return out[:ENTRY_CAP], total

    def callers_of(self, system_id: str, loc: tuple[str, int] | None = None) -> list[str]:
        """Code using an external system: CONNECTS_TO sources (the ones at `loc` when given), logical connections
        resolved to their users, ORM tables resolved to their readers / writers."""
        edges = self.conn_edges(system_id)
        if loc:
            at = [e for e in edges if e["file"] == loc[0] and e["line"] == loc[1]]
            edges = at or edges
        out = set()
        for e in edges:
            s = e["src"]
            if s.startswith("connection:"):
                out |= {y["src"] for y in self.st.q("SELECT src FROM edges WHERE dst=? AND kind='USES_CONNECTION'", (s,))}
            elif s.startswith("table:"):
                out |= {y["src"] for y in self.st.q(
                    "SELECT src FROM edges WHERE dst=? AND kind IN ('READS_TABLE','WRITES_TABLE','MAPS_TO_TABLE')", (s,))}
            else:
                out.add(s)
        return sorted(out)


def _group(rows, key: str) -> dict:
    out = defaultdict(list)
    for r in rows:
        out[r[key]].append(dict(r))
    return out


def fingerprint(finding: str, node: str, file: str | None) -> str:
    return hashlib.sha256(f"{finding}\0{node}\0{file or ''}".encode()).hexdigest()[:16]


def _split_loc(loc: str | None) -> tuple[str, int] | None:
    """`path:line` -> (path, line); None for `env:KEY` / `config:x` references and anything else."""
    if not loc:
        return None
    m = _LOC.match(loc)
    if not m or m["file"] in ("env", "config", "connection", "const", "constant"):
        return None
    return m["file"], int(m["line"])


def make_finding(ctx: Ctx, finding: str, *, protocol: str | None, node: str, file: str | None, line: int | None,
                 confidence: str, detail: str, entry_ids=(), repo: str | None = None, attrs: dict | None = None,
                 see: str | None = None, entry_points=None) -> dict:
    repo = ctx.repo_of(None, file) or repo or ctx.repo_of(attrs, None)
    if entry_points is None:
        eps, total = ctx.entry_points(entry_ids)
    else:
        eps, total = entry_points
    out = {"finding": finding, "severity": FINDINGS[finding]["severity"], "confidence": confidence or "heuristic",
           "protocol": protocol, "node": node, "file": file, "line": line, "entry_points": eps,
           "entry_point_total": total, "detail": detail, "fingerprint": fingerprint(finding, node, file)}
    if repo:
        out["repo"] = repo
    if see:
        out["see"] = see
    return out


# ------------------------------------------------------------------ hardcoded
SECRET_WORDS = ("PASSWORD", "PASS", "PASSWD", "SECRET", "TOKEN", "SECRET_KEY", "SECRET_ACCESS_KEY", "KEY", "AUTH", "PWD")


def secret_key(key: str) -> bool:
    k = re.sub(r"[^A-Za-z0-9]+", "_", key).upper().strip("_")
    return any(k.endswith("_" + s) or k == s for s in SECRET_WORDS) and not k.endswith(("_KEY_ID", "_PUBLIC_KEY", "_KEY_PATH"))


def _kind_of_key(key: str) -> str:
    k = re.sub(r"[^A-Za-z0-9]+", "_", key).upper()
    return "token" if "TOKEN" in k else "key" if k.endswith("KEY") else "secret" if k.endswith("SECRET") else "password"


def _cred_loc(ctx: Ctx, nid: str, a: dict) -> tuple[str | None, int | None]:
    """Where a system's literal credential is, as stored in the graph (`<repo>/<path>` on a combined one):
    credential_at (`file:line` or a config / const node id), else the edge flagged `literal_credential`, else the
    address location."""
    repo = ctx.repo_of(a, None)
    at = a.get("credential_at")
    loc = _split_loc(at)
    if loc:
        return ctx.full_file(repo, loc[0]), loc[1]
    if at and ctx.st.node(at):
        f, ln = ctx.node_loc(at)
        if f:
            return f, ln
    for e in ctx.conn_edges(nid):
        if json.loads(e["attrs"] or "{}").get("literal_credential") and e["file"]:
            return e["file"], e["line"]
    loc = _split_loc(a.get("address_at"))
    return (ctx.full_file(repo, loc[0]), loc[1]) if loc else (None, None)


@producer("hardcoded")
def _hardcoded(ctx: Ctx, strict: bool = False) -> list[dict]:
    out = []
    for s in ctx.externals:
        a, nid = s["attrs"], s["id"]
        proto = a.get("protocol")
        repo = ctx.repo_of(a, None)
        see = f"cg external '{nid}'"
        in_url = bool(a.get("credential_in_url"))
        literal = a.get("credential_source") == "literal" or a.get("credential_literal")
        if literal:
            file, line = _cred_loc(ctx, nid, a)
            if file is None:
                file, line = ctx.node_loc(nid)
            kind = "key" if proto == "saas" else _kind_of_key(str(a.get("credential_at") or "")) if str(a.get("credential_at") or "").startswith(("config:", "const")) else "password"
            eps = ctx.callers_of(nid, (file, line) if file else None)
            out.append(make_finding(ctx, "hardcoded", protocol=proto, node=nid, file=file, line=line,
                                    confidence=a.get("confidence"), repo=repo, attrs=a, see=see, entry_ids=eps,
                                    detail=f"literal {kind} in code or configuration for {a.get('target') or nid} (value not shown)"))
        if in_url:
            loc = _split_loc(a.get("address_at"))
            file, line = (ctx.full_file(repo, loc[0]), loc[1]) if loc else ctx.node_loc(nid)
            out.append(make_finding(ctx, "hardcoded", protocol=proto, node=nid, file=file, line=line,
                                    confidence=a.get("confidence"), repo=repo, attrs=a, see=see,
                                    entry_ids=ctx.callers_of(nid),
                                    detail=f"password inline in the {a.get('scheme') or proto} connection URL (value not shown)"))
        for e in ctx.conn_edges(nid):
            ea = json.loads(e["attrs"] or "{}")
            if ea.get("literal_credential") and e["file"]:
                erepo = ctx.repo_of(None, e["file"]) or repo
                out.append(make_finding(ctx, "hardcoded", protocol=proto, node=nid, file=e["file"], line=e["line"],
                                        confidence=e["confidence"], repo=erepo, attrs=a, see=see, entry_ids=[e["src"]],
                                        detail=f"literal credential passed to {ea.get('via') or proto} (value not shown)"))
    here = {(f["file"], f["line"]) for f in out}
    out += [f for f in _config_literals(ctx) if (f["file"], f["line"]) not in here or "default" in f["detail"]]
    return _dedupe(out)


def _placeholder(v) -> bool:
    if not isinstance(v, str):
        return True
    t = v.strip()
    return not t or t.startswith(("${", "{{", "%(", "<", "$")) or t.lower() in ("null", "none", "false", "true", "changeme?")


def _config_literals(ctx: Ctx) -> list[dict]:
    """Config nodes (Laravel config/*.php, ...) whose key names a secret and whose value is a literal, or whose
    `env('X', 'default')` default is one. Only the key and the location are reported."""
    out = []
    for r in ctx.st.q("SELECT id, name, file, line, attrs FROM nodes WHERE kind='config'"):
        a = json.loads(r["attrs"] or "{}")
        key = r["name"] or r["id"][len("config:"):]
        last = key.split(".")[-1]
        repo = ctx.repo_of(a, r["file"])
        if secret_key(last) and not _placeholder(a.get("value")):
            out.append(make_finding(ctx, "hardcoded", protocol="config", node=r["id"], file=r["file"], line=r["line"],
                                    confidence="resolved", repo=repo, attrs=a, see=f"cg node '{r['id']}'",
                                    entry_ids=[r["id"]], detail=f"literal {_kind_of_key(last)} in config key {key} (value not shown)"))
        envs, defaults = a.get("env") or [], a.get("env_default") or []
        for env, dflt in zip(envs, defaults):
            if secret_key(str(env)) and not _placeholder(dflt):
                out.append(make_finding(ctx, "hardcoded", protocol="config", node=r["id"], file=r["file"], line=r["line"],
                                        confidence="heuristic", repo=repo, attrs=a, see=f"cg node '{r['id']}'",
                                        entry_ids=[r["id"]], detail=f"literal default for env {env} in config key {key} (value not shown)"))
    return out


# ------------------------------------------------------------------ plaintext
INHERENT_PLAINTEXT = {"telnet", "tftp"}


def _host_of(a: dict) -> str | None:
    h = a.get("host")
    if h:
        return str(h).lower()
    t = str(a.get("target") or "")
    if t.startswith(("env:", "config:")) or not t:
        return None
    return t.rsplit(":", 1)[0].lower() if ":" in t else t.lower()


def is_loopback(host: str | None) -> bool:
    return host is None or host in LOOPBACK or host.startswith("127.") or host.endswith(".localhost")


@producer("plaintext")
def _plaintext(ctx: Ctx, strict: bool = False) -> list[dict]:
    out = []
    for s in ctx.externals:
        a, nid = s["attrs"], s["id"]
        proto, host, port = a.get("protocol"), _host_of(a), a.get("port")
        tls, why, conf = a.get("tls"), None, a.get("confidence")
        if tls is False:
            why = f"{a.get('scheme') or proto} without TLS"
        elif tls is None and proto in INHERENT_PLAINTEXT:
            why, conf = f"{proto} has no transport protection", "heuristic"
        elif tls is None and proto == "mqtt" and port == 1883:
            why, conf = "MQTT on the plaintext port 1883", "heuristic"
        elif tls is None and proto == "smtp" and port == 25:
            why, conf = "SMTP on port 25 (no TLS)", "heuristic"
        if not why or host is None or is_loopback(host):
            continue
        loc = _split_loc(a.get("address_at"))
        file, line = (ctx.full_file(ctx.repo_of(a, None), loc[0]), loc[1]) if loc else (None, None)
        if file is None:
            edges = [e for e in ctx.conn_edges(nid) if e["file"]]
            if edges:
                file, line = edges[0]["file"], edges[0]["line"]
            else:
                file, line = ctx.node_loc(nid)
        repo = ctx.repo_of(None, file) or ctx.repo_of(a, None)
        where = f"{host}:{port}" if port else host
        out.append(make_finding(ctx, "plaintext", protocol=proto, node=nid, file=file, line=line, confidence=conf,
                                repo=repo, attrs=a, see=f"cg external '{nid}'", entry_ids=ctx.callers_of(nid),
                                detail=f"{why}: {where}"))
    return _dedupe(out)


# ------------------------------------------------------------------ inbound
class Inbound:
    """Inbound items from routes (routes_report) and generic endpoints / messages / jobs (protocols view)."""

    def __init__(self, ctx: Ctx, strict: bool = False):
        self.ctx, self.strict = ctx, strict
        self.items: list[dict] = []
        self._build()

    def _write_closure(self) -> set[str]:
        from . import query as Q
        st = self.ctx.st
        targets = [r["dst"] for r in st.q("SELECT DISTINCT dst FROM edges WHERE kind IN ('WRITES_TABLE','WRITES_COLUMN')")]
        if not targets:
            return set()
        return set(Q.reverse_closure(st, targets, kinds=PROPAGATING, exclude_gate=Q.default_gate(st)))

    def _build(self):
        from .protocols.view import collect
        from .routes import routes_report
        ctx = self.ctx
        st = ctx.st
        routes = routes_report(st, strict=self.strict)
        writes = {i["route"] for i in routes_report(st, writes="*", strict=self.strict)["items"]}
        node_rows = {r["id"]: dict(r) for r in st.q("SELECT id, kind, file, line, lang, entry_kind, attrs FROM nodes WHERE kind='route'")}
        for it in routes["items"]:
            n = node_rows.get(it["route"], {})
            a = json.loads(n.get("attrs") or "{}")
            if a.get("test_only") or a.get("mounted", True) is False:
                continue
            from .protocols.view import protocol_of
            proto = protocol_of("route", it["route"], a, n.get("lang")) or "http"
            wh = it.get("webhook")
            if it["has_auth"]:
                state = "guarded"
            elif it["secret_checked"]:
                state = "secret-checked"
            elif it.get("inline_auth") and not self.strict:
                state = "inline-guarded"
            else:
                state = "unguarded"
            self.items.append(self._item(
                node=it["route"], protocol=proto, transport="ws" if proto == "ws" else "http", name=it["name"],
                file=n.get("file"), line=n.get("line"), attrs=a, state=state,
                guards=[g["name"] for g in it["guards"]] + [g["name"] for g in it["inline_guards"]],
                writes=it["route"] in writes, confidence="resolved", entry_kind=n.get("entry_kind") or "http_route",
                handler_ids=[it["route"]], extra={"webhook": wh} if wh else {}))
        closure = self._write_closure()
        for nid, n in sorted(collect(st).items()):
            if n["kind"] in ("route", "http", "channel", "channel_sub", "event") or n["protocol"] in NOT_INBOUND:
                continue
            a = n["attrs"]
            receivers = [r for r in n["receivers"] if r.get("process") != "client"]
            if a.get("test_only") or not (receivers or (n.get("served") and not n["receivers"])):
                continue
            recv = receivers[0] if receivers else {}
            loc = _split_loc(recv.get("at")) or (n["file"], n["line"])
            handlers = [r["handler"] for r in receivers]
            guards = n.get("guards")
            if "unguarded" in n["checks"]:
                state = "unguarded"
            elif guards is None:
                state = "unchecked"
            else:
                state = "guarded" if guards else "unchecked"
            ex = {k: a[k] for k in ("exposure", "bind_addresses", "port", "mode", "exported", "permission") if a.get(k) not in (None, "")}
            rex = recv.get("exposure")
            if rex and "exposure" not in ex:
                ex["exposure"] = rex
            conf = _best_conf([r["confidence"] for r in receivers]) or "resolved"
            kinds = [x["entry_kind"] for x in (dict(r) for r in st.q(
                f"SELECT entry_kind FROM nodes WHERE id IN ({','.join('?' * len(handlers))})", handlers))] if handlers else []
            self.items.append(self._item(
                node=nid, protocol=n["protocol"], transport=a.get("transport") or "tcp", name=n["name"], file=loc[0],
                line=loc[1], attrs=a, state=state, guards=list(guards or []), writes=any(h in closure for h in handlers),
                confidence=conf, entry_kind=next((k for k in kinds if k), "message_handler"), handler_ids=[nid],
                extra=ex))

    def _item(self, *, node, protocol, transport, name, file, line, attrs, state, guards, writes, confidence,
              entry_kind, handler_ids, extra) -> dict:
        ctx = self.ctx
        repo = ctx.repo_of(attrs, file)
        out = {"node": node, "name": name, "protocol": protocol, "transport": transport, "file": file, "line": line,
               "guard_state": state, "guards": guards, "reaches_write": bool(writes), "confidence": confidence,
               "entry_points": [{"kind": entry_kind, "count": 1, "sample": node}], "entry_point_total": 1, **extra}
        if repo:
            out["repo"] = repo
        return out


def _best_conf(confs) -> str | None:
    confs = [c for c in confs if c in CONFIDENCE_RANK]
    return max(confs, key=CONFIDENCE_RANK.get) if confs else None


def _inbound(ctx: Ctx, strict: bool) -> Inbound:
    return ctx.cached(("inbound", strict), lambda: Inbound(ctx, strict))


@producer("unverified")
def _unverified(ctx: Ctx, strict: bool = False) -> list[dict]:
    out = []
    for it in _inbound(ctx, strict).items:
        wh = it.get("webhook")
        if not wh or wh.get("verified") is not False:
            continue
        hdrs = wh.get("headers") or wh.get("signature_headers")
        out.append(make_finding(
            ctx, "unverified", protocol=it["protocol"], node=it["node"], file=it["file"], line=it["line"],
            confidence="resolved", repo=it.get("repo"), see=f"cg routes --reaches {it['node']}",
            entry_points=(it["entry_points"], it["entry_point_total"]),
            detail=f"{wh.get('provider') or 'webhook'} webhook {it['name']} reads provider data and never verifies the "
                   f"signature or secret" + (f" (headers: {', '.join(map(str, hdrs))})" if hdrs else "")))
    return out


@producer("unguarded")
def _unguarded(ctx: Ctx, strict: bool = False) -> list[dict]:
    out = []
    for it in _inbound(ctx, strict).items:
        if it["guard_state"] != "unguarded":
            continue
        w = " and reaches a write" if it["reaches_write"] else ""
        out.append(make_finding(
            ctx, "unguarded", protocol=it["protocol"], node=it["node"], file=it["file"], line=it["line"],
            confidence=it["confidence"], repo=it.get("repo"),
            see=f"cg routes --unguarded" if it["node"].startswith("route:") else f"cg protocols --pattern '{it['node']}'",
            entry_points=(it["entry_points"], it["entry_point_total"]),
            detail=f"{it['name']} has no auth guard{w}"))
    return out


@producer("exposed-listener")
def _exposed(ctx: Ctx, strict: bool = False) -> list[dict]:
    out = []
    for it in _inbound(ctx, strict).items:
        if it.get("exposure") != "all":
            continue
        port = it.get("port")
        out.append(make_finding(
            ctx, "exposed-listener", protocol=it["protocol"], node=it["node"], file=it["file"], line=it["line"],
            confidence=it["confidence"], repo=it.get("repo"), see=f"cg protocols --listeners",
            entry_points=(it["entry_points"], it["entry_point_total"]),
            detail=f"{it['transport']} listener{f' on port {port}' if port else ''} bound to all interfaces"))
    return out


# ------------------------------------------------------------------ outbound surface
def _outbound(ctx: Ctx) -> list[dict]:
    items = []
    for s in ctx.externals:
        a, nid = s["attrs"], s["id"]
        repo = ctx.repo_of(a, None)
        eps, total = ctx.entry_points(ctx.callers_of(nid))
        loc = _split_loc(a.get("address_at")) or (None, None)
        item = {"node": nid, "protocol": a.get("protocol"), "target": a.get("target"),
                "address_source": a.get("address_source"), "address_at": _abs_loc(ctx, repo, a.get("address_at")),
                "credential_source": a.get("credential_source"), "credential_at": _abs_loc(ctx, repo, a.get("credential_at")),
                "auth": a.get("auth"), "tls": a.get("tls"), "confidence": a.get("confidence") or "exact",
                "file": ctx.full_file(repo, loc[0]), "line": loc[1], "entry_points": eps, "entry_point_total": total}
        if a.get("credential_literal") or a.get("credential_in_url"):
            item["credential_source"] = "literal"
        if repo:
            item["repo"] = repo
        items.append({k: v for k, v in item.items() if v is not None})
    return items


def _abs_loc(ctx: Ctx, repo: str | None, loc: str | None) -> str | None:
    p = _split_loc(loc)
    if not p:
        return loc
    return f"{ctx.full_file(repo, p[0])}:{p[1]}"


# ------------------------------------------------------------------ ignores
def _dedupe(rows: list[dict]) -> list[dict]:
    """One finding per fingerprint (finding + node + file): the earliest line, the other lines listed."""
    groups: dict[str, list[dict]] = {}
    for f in rows:
        groups.setdefault(f["fingerprint"], []).append(f)
    out = []
    for fs in groups.values():
        lines = sorted({f["line"] for f in fs if f["line"]})
        first = min(fs, key=lambda f: f["line"] or 0) if lines else fs[0]
        first = dict(first)
        if not first["entry_points"]:
            first["entry_points"], first["entry_point_total"] = next(
                ((f["entry_points"], f["entry_point_total"]) for f in fs if f["entry_points"]), ([], 0))
        if len(lines) > 1:
            first["also_at_lines"] = [x for x in lines if x != first["line"]]
        out.append(first)
    return out


def load_ignores(st: GraphStore) -> dict[str | None, list[dict]]:
    """`surface.ignore` entries per repo (None for a single-repo graph). The file is re-read from the indexed root
    when it still exists, so an accepted risk takes effect without re-indexing; else the config recorded at index time."""
    from . import config as C
    out: dict[str | None, list[dict]] = {}
    for repo, recorded, root in C.graph_configs(st):
        cfg = recorded or {}
        if root and Path(root).is_dir():
            try:
                cfg = C.load(root) or cfg
            except C.ConfigError:
                pass
        out[repo] = list((cfg.get("surface") or {}).get("ignore") or [])
    return out


def _ignore_match(f: dict, rule: dict, ctx: Ctx) -> bool:
    if rule["finding"] not in ("*", f["finding"]):
        return False
    if "fingerprint" in rule and rule["fingerprint"] != f["fingerprint"]:
        return False
    if "id" in rule and not (rule["id"] == f["node"] or fnmatch.fnmatchcase(f["node"], rule["id"])):
        return False
    if "path" in rule:
        full = f.get("file") or ""
        rel = ctx.rel_file(f.get("repo"), full) or ""
        pat = rule["path"].rstrip("/")
        if not any(fnmatch.fnmatchcase(p, pat) or p == pat or p.startswith(pat + "/") for p in (full, rel)):
            return False
    return True


def apply_ignores(ctx: Ctx, findings: list[dict]) -> tuple[list[dict], list[dict]]:
    rules = load_ignores(ctx.st)
    kept, ignored = [], []
    for f in findings:
        mine = rules.get(f.get("repo")) if ctx.combined else rules.get(None)
        hit = next((r for r in mine or [] if _ignore_match(f, r, ctx)), None)
        if hit:
            ignored.append({**f, "ignored_by": {k: v for k, v in hit.items()}})
        else:
            kept.append(f)
    return kept, ignored


# ------------------------------------------------------------------ query
def _sort_key(f: dict):
    return (list(FINDINGS).index(f["finding"]), f.get("protocol") or "", f.get("repo") or "", f.get("file") or "",
            f.get("line") or 0, f["node"])


def surface(st: GraphStore, inbound: bool = False, outbound: bool = False, protocol: str | None = None,
            finding: str | None = None, min_confidence: str = "heuristic", fail_on: list[str] | None = None,
            max_items: int = 200, strict: bool = False, show_ignored: bool = False) -> dict:
    """The structure behind `cg surface --format json`.

    No view flag: findings plus per-surface counts. `inbound` / `outbound`: that surface's items and the findings
    of that direction. `finding`: only that finding type. `fail_on` is judged on every finding type (after
    ignores, `protocol` and `min_confidence`), not only the ones shown."""
    if finding and finding not in FINDINGS:
        raise ValueError(f"unknown finding {finding!r} (one of {', '.join(FINDINGS)})")
    for f in fail_on or []:
        if f not in FINDINGS:
            raise ValueError(f"unknown finding {f!r} in --fail-on (one of {', '.join(FINDINGS)})")
    floor = CONFIDENCE_RANK[min_confidence]
    ctx = Ctx(st)
    allf: list[dict] = []
    for fn in _PRODUCERS.values():
        allf += fn(ctx, strict)

    def keep(x):
        return (not protocol or x.get("protocol") == protocol) and CONFIDENCE_RANK.get(x.get("confidence"), 1) >= floor

    allf = sorted((f for f in allf if keep(f)), key=_sort_key)
    kept, ignored = apply_ignores(ctx, allf)
    failing = sorted({f["finding"] for f in kept if f["finding"] in (fail_on or [])})
    shown = kept
    directions = {d for d, on in (("inbound", inbound), ("outbound", outbound)) if on}
    if finding:
        shown = [f for f in shown if f["finding"] == finding]
    elif directions:
        shown = [f for f in shown if FINDINGS[f["finding"]]["direction"] in directions]
    shown_ignored = [f for f in ignored if f["finding"] == finding] if finding else ignored
    counts = {"findings": len(shown), "by_finding": _count(shown, "finding"), "by_severity": _count(shown, "severity"),
              "ignored": len(shown_ignored)}
    res = {"filters": {"inbound": inbound, "outbound": outbound, "protocol": protocol, "finding": finding,
                       "min_confidence": min_confidence, "strict": strict},
           "combined": ctx.combined, "summary": counts, "findings": shown[:max_items],
           "findings_total": len(shown), "ignored_count": len(shown_ignored), "fail_on": fail_on or [],
           "failing": failing, "failed": bool(failing)}
    if ctx.combined:
        res["repos"] = ctx.repos
    if show_ignored:
        res["ignored"] = shown_ignored[:max_items]
    inb_items = [i for i in _inbound(ctx, strict).items if keep(i)]
    out_items = [o for o in _outbound(ctx) if keep(o)]
    res["surface_counts"] = {"inbound": len(inb_items), "outbound": len(out_items),
                             "inbound_by_state": _count(inb_items, "guard_state"),
                             "inbound_by_protocol": _count(inb_items, "protocol"),
                             "outbound_by_protocol": _count(out_items, "protocol")}
    if inbound:
        res["inbound"] = sorted(inb_items, key=lambda i: (i["protocol"], i.get("repo") or "", i.get("file") or "",
                                                          i.get("line") or 0, i["node"]))[:max_items]
    if outbound:
        res["outbound"] = sorted(out_items, key=lambda i: (i["protocol"] or "", i["node"]))[:max_items]
    return res


def _count(rows, key: str) -> dict:
    c: dict = {}
    for r in rows:
        c[r.get(key)] = c.get(r.get(key), 0) + 1
    return dict(sorted(c.items(), key=lambda kv: str(kv[0])))


# ------------------------------------------------------------------ renderers
def _eps_text(f: dict) -> str:
    eps = f.get("entry_points") or []
    if not eps:
        return "no entry point"
    more = f.get("entry_point_total", 0) - sum(e["count"] for e in eps)
    return ", ".join(f"{e['kind']}({e['count']})" for e in eps) + (f" ... {more} more" if more > 0 else "")


def _where(f: dict) -> str:
    repo = f"[{f['repo']}] " if f.get("repo") else ""
    if not f.get("file"):
        return repo + "(no source location)"
    return f"{repo}{f['file']}:{f['line']}" if f.get("line") else f"{repo}{f['file']}"


def _render_findings(rows: list[dict], L: list[str]) -> None:
    by: dict[str, dict[str, list]] = defaultdict(lambda: defaultdict(list))
    for f in rows:
        by[f["finding"]][f.get("protocol") or "-"].append(f)
    for name in FINDINGS:
        if name not in by:
            continue
        n = sum(len(v) for v in by[name].values())
        L += ["", f"== {name} ({n}) - {FINDINGS[name]['title']} =="]
        for proto, fs in sorted(by[name].items()):
            L.append(f"  {proto}")
            for f in fs:
                L.append(f"    {f['severity'].upper()} [{f['confidence']}] {f['node']}")
                L.append(f"      {f['detail']}")
                L.append(f"      at {_where(f)}" + (f" (also lines {', '.join(map(str, f['also_at_lines']))})"
                                                      if f.get("also_at_lines") else ""))
                L.append(f"      reached from {_eps_text(f)}")
                L.append(f"      fingerprint {f['fingerprint']}" + (f"  ignored: {f['ignored_by']['reason']}" if f.get("ignored_by") else ""))


def render_surface(res: dict, max_items: int = 200) -> str:
    s = res["summary"]
    sev = ", ".join(f"{v} {k}" for k, v in sorted(s["by_severity"].items(), key=lambda kv: SEVERITY_ORDER.get(kv[0], 9)))
    L = [f"attack surface: {s['findings']} finding(s)" + (f" ({sev})" if sev else "")
         + (f", {res['ignored_count']} ignored (--show-ignored lists them)" if res["ignored_count"] and "ignored" not in res else
            f", {res['ignored_count']} ignored" if res["ignored_count"] else "")]
    c = res["surface_counts"]
    L.append(f"inbound surface: {c['inbound']} item(s)"
             + (" (" + ", ".join(f"{v} {k}" for k, v in c["inbound_by_state"].items()) + ")" if c["inbound_by_state"] else "")
             + f"; outbound surface: {c['outbound']} system(s)")
    if res["findings_total"] > len(res["findings"]):
        L.append(f"showing {len(res['findings'])} of {res['findings_total']} findings (--max-items)")
    _render_findings(res["findings"][:max_items], L)
    if not res["findings"]:
        L += ["", "no findings" + (" for these filters" if any(res["filters"][k] for k in ("protocol", "finding")) else "")]
    if "inbound" in res:
        L += ["", f"== inbound surface ({c['inbound']}) =="]
        for i in res["inbound"]:
            bits = [i["transport"], i["guard_state"] + (f" ({', '.join(i['guards'][:4])})" if i["guards"] else "")]
            if i.get("webhook"):
                bits.append(f"webhook {i['webhook'].get('provider')} " + ("verified" if i["webhook"].get("verified") else "NOT verified"))
            if i.get("exposure"):
                bits.append(f"bind {i['exposure']}")
            if i["reaches_write"]:
                bits.append("reaches a write")
            L.append(f"  {i['protocol']:<9} {i['name']}  [{i['confidence']}]")
            L.append(f"            {'; '.join(bits)}")
            L.append(f"            at {_where(i)}")
    if "outbound" in res:
        L += ["", f"== outbound surface ({c['outbound']}) =="]
        for o in res["outbound"]:
            bits = [f"address {o.get('address_source')}" + (f" @ {o['address_at']}" if o.get("address_at") else "")]
            if o.get("credential_source"):
                bits.append(f"credentials {o['credential_source']}" + (f" @ {o['credential_at']}" if o.get("credential_at") else ""))
            elif o.get("auth"):
                bits.append(f"auth {o['auth']}")
            if o.get("tls") is not None:
                bits.append("tls" if o["tls"] else "plaintext")
            L.append(f"  {o['node']}  [{o['confidence']}]" + (f"  [{o['repo']}]" if o.get("repo") else ""))
            L.append("      " + "; ".join(bits))
            L.append(f"      reached from {_eps_text(o)}")
    if res.get("ignored"):
        L += ["", f"== ignored ({len(res['ignored'])}) =="]
        _render_findings(res["ignored"], L)
    if res["fail_on"]:
        L += ["", ("gate FAILED: " + ", ".join(res["failing"])) if res["failed"] else
              f"gate passed (--fail-on {','.join(res['fail_on'])})"]
    return "\n".join(L)


LEVEL = {"high": "error", "medium": "warning", "low": "note"}


def to_sarif(res: dict) -> dict:
    """SARIF 2.1.0: one rule per finding type, `level` from severity, repo-relative locations, partialFingerprints."""
    from . import __version__
    names = list(FINDINGS)
    rules = [{"id": f"cg.surface.{n}", "name": n, "shortDescription": {"text": FINDINGS[n]["title"]},
              "fullDescription": {"text": FINDINGS[n]["help"]}, "help": {"text": FINDINGS[n]["help"]},
              "defaultConfiguration": {"level": LEVEL[FINDINGS[n]["severity"]]},
              "properties": {"tags": ["security", FINDINGS[n]["direction"]]}} for n in names]

    def result(f: dict, suppressed: bool = False) -> dict:
        repo = f.get("repo")
        uri = f["file"]
        if repo and uri and uri.startswith(repo + "/"):
            uri = uri[len(repo) + 1:]
        r = {"ruleId": f"cg.surface.{f['finding']}", "ruleIndex": names.index(f["finding"]), "level": LEVEL[f["severity"]],
             "message": {"text": f["detail"]},
             "partialFingerprints": {"cg/surface/v1": f["fingerprint"]},
             "properties": {"confidence": f["confidence"], "protocol": f.get("protocol"), "node": f["node"],
                            "entryPoints": [f"{e['kind']}({e['count']})" for e in f["entry_points"]],
                            "entryPointTotal": f["entry_point_total"], **({"repo": repo} if repo else {})}}
        if uri:
            r["locations"] = [{"physicalLocation": {"artifactLocation": {"uri": uri, "uriBaseId": "%SRCROOT%"},
                                                    "region": {"startLine": max(int(f.get("line") or 1), 1)}}}]
        if suppressed:
            r["suppressions"] = [{"kind": "external", "justification": f["ignored_by"]["reason"]}]
        return r

    results = [result(f) for f in res["findings"]] + [result(f, True) for f in res.get("ignored", [])]
    return {"$schema": SARIF_SCHEMA, "version": "2.1.0",
            "runs": [{"tool": {"driver": {"name": "cg", "version": __version__, "informationUri":
                                          "https://github.com/cyberchronos00/code-graph", "rules": rules}},
                      "results": results}]}


def to_json(res: dict) -> str:
    return json.dumps(res, indent=1, default=str)
