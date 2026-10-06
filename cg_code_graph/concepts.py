"""Deterministic concept queries over resolution facts.

`resolutions(st, concept)` lists every place a value for the concept (e.g. "currency", "timezone", "locale") is *resolved*: a variable,
fallback return or resolver function whose value comes from an ordered fallback chain (request key, JSON setting
and its default, model attribute / queried column, config/env, literal). Sites are grouped by their chain
signature, divergent chains are compared step by step, and on a combined graph the client side is attached:
which frontend endpoints reach each site, whether their requests can/do send the concept's request key, and the
client's own literal fallbacks (`report.value?.timezone ?? 'UTC'`).

Matching is lexical and deterministic: a site belongs to the concept when the *head word* of its target
(`$timezone`, `$customerTimezone`, `resolveTimezone()`, `report.value?.timezone`) is the concept (singular/plural),
or when its chain starts with a request key / setting / column whose head word is the concept.
The output is the deterministic facts themselves, ready for a reviewer or an agent to summarise.
"""
from __future__ import annotations

import json
import re
from collections import defaultdict

from .core.store import GraphStore

SOURCE_KINDS = ("input", "setting", "column", "config", "env")
CALL_LIKE_UP = ["CALLS", "IMPLEMENTED_BY", "OVERRIDDEN_BY", "BOUND_TO", "ROUTES_TO", "HANDLED_BY"]


def head_token(ident: str) -> str:
    toks = re.findall(r"[A-Z]?[a-z0-9]+|[A-Z]+(?![a-z])", (ident or "").replace("$", ""))
    return toks[-1].lower() if toks else ""


def concept_forms(concept: str) -> set[str]:
    c = concept.lower().strip()
    forms = {c}
    if c.endswith("ies"):
        forms.add(c[:-3] + "y")
    elif c.endswith("y"):
        forms.add(c[:-1] + "ies")
    elif c.endswith("s"):
        forms.add(c[:-1])
    else:
        forms.add(c + "s")
    return forms


def short(nid: str | None) -> str:
    if not nid:
        return "?"
    x = nid.split(":", 1)[-1]
    if "#" in x and nid.split(":", 1)[0] in ("page", "component", "layout", "composable", "function", "store", "module"):
        return x
    x = x.split("\\")[-1] if "\\" in x and "::" not in x else x
    if "::" in x:
        cls, m = x.rsplit("::", 1)
        x = f"{cls.split(chr(92))[-1]}::{m}"
    return x


def flat_atoms(atoms):
    for a in atoms or []:
        if a["kind"] == "call" and a.get("atoms"):
            yield from flat_atoms(a["atoms"])
        elif a["kind"] == "param" and a.get("sources"):
            seen = []
            for s in a["sources"]:
                for x in flat_atoms(s["atoms"]):
                    k = json.dumps({kk: vv for kk, vv in x.items() if kk in ("kind", "key", "column", "value", "text")}, sort_keys=True)
                    if k not in seen:
                        seen.append(k)
                        yield {**x, "via_param": a["name"], "callers": len(a["sources"])}
        else:
            yield a


def atom_text(a) -> str:
    k = a["kind"]
    norm = f" [{', '.join(a['norm'])}]" if a.get("norm") else ""
    if k == "input":
        flow = f"; flow: {' <- '.join(a['flow'])}" if a.get("flow") else ""
        vp = f" (param ${a['via_param']} from {a['callers']} call site(s))" if a.get("via_param") else ""
        return f"request key '{a['key']}' via {a.get('via')}{vp}{flow}{norm}"
    if k == "setting":
        own = f" ({'/'.join(a['owner'])}::getSetting)" if a.get("owner") else ""
        return f"setting '{a['key']}'{own} default {a.get('default')!r}{norm}"
    if k == "column":
        if a.get("candidates"):
            return f"column {' | '.join(a['candidates'])} ({a.get('how')}, one per model){norm}"
        return f"column {a['column']} ({a.get('how')}){norm}"
    if k in ("config", "env"):
        return f"{k} '{a['key']}' default {a.get('default')!r}"
    if k == "literal":
        src = f" ({a['from']})" if a.get("from") else ""
        return f"literal {a.get('value')!r}{src}"
    if k == "call":
        return f"call {short(a['target'])}()"
    if k == "param":
        return f"param ${a['name']} (no resolvable callers)"
    return f"expr {a.get('text')}"


def _matches(attrs: dict, forms: set[str], lang: str) -> str | None:
    tname = attrs.get("target_name") or attrs.get("target") or ""
    if lang == "ts":
        m = re.findall(r"[A-Za-z_$][\w$]*", tname)
        if m and head_token(m[-1]) in forms:
            return "target"
        return None
    if head_token(tname) in forms:
        return "target"
    sig = attrs.get("signature") or []
    if sig:
        k, _, key = sig[0].partition(":")
        if k in SOURCE_KINDS and head_token(key) in forms:
            return "first-source"
    return None


def _routes_reaching(st: GraphStore, fn: str, max_depth=12) -> list[str]:
    from .query import reverse_closure
    d = reverse_closure(st, [fn], kinds=CALL_LIKE_UP, max_depth=max_depth)
    return sorted(n for n in d if n.startswith("route:"))


def _diff(a: list[str], b: list[str]) -> dict:
    i = 0
    while i < min(len(a), len(b)) and a[i] == b[i]:
        i += 1
    return {"common_prefix": a[:i], "a_next": a[i] if i < len(a) else None, "b_next": b[i] if i < len(b) else None,
            "final_a": a[-1] if a else None, "final_b": b[-1] if b else None}


def resolutions(st: GraphStore, concept: str, within: str | None = None, client: bool = True) -> dict:
    forms = concept_forms(concept)
    w = within.lower() if within else None
    rows = st.q("SELECT id, name, file, line, lang, attrs FROM nodes WHERE kind='resolution'")
    backend, frontend = [], []
    for r in rows:
        a = json.loads(r["attrs"] or "{}")
        how = _matches(a, forms, r["lang"])
        if not how:
            continue
        site = {"id": r["id"], "fn": a.get("fn"), "target": a.get("target"), "form": a.get("form"), "file": r["file"],
                "line": r["line"], "signature": a.get("signature") or [], "chain": a.get("chain") or [],
                "final_literal": a.get("final_literal"), "match": how, "repo": a.get("repo")}
        (frontend if r["lang"] == "ts" else backend).append(site)
    if w:
        backend = [s for s in backend if w in (s["fn"] or "").lower() or w in (s["file"] or "").lower()]
    backend.sort(key=lambda s: (s["file"] or "", s["line"] or 0))
    # chains by signature
    groups: dict[tuple, list] = defaultdict(list)
    for s in backend:
        groups[tuple(s["signature"])].append(s)
    chains = []
    for i, (sig, sites) in enumerate(sorted(groups.items(), key=lambda kv: (-len(kv[1]), kv[0]))):
        label = chr(ord("A") + i) if i < 26 else chr(ord("A") + i // 26 - 1) + chr(ord("A") + i % 26)
        for s in sites:
            s["chain_label"] = label
        chains.append({"label": label, "signature": list(sig), "sites": [s["id"] for s in sites]})
    # request-driven sites: chain contains a request key of the concept
    concept_keys = sorted({x.split(":", 1)[1] for s in backend for x in s["signature"]
                           if x.startswith("input:") and head_token(x.split(":", 1)[1]) in forms})
    req_sites = [s for s in backend if any(x.startswith("input:") and x.split(":", 1)[1] in concept_keys for x in s["signature"])]
    # routes per site (who can trigger it)
    for s in backend:
        s["routes"] = _routes_reaching(st, s["fn"]) if s["fn"] else []
    divergence = []
    by_label = {c["label"]: c for c in chains}
    req_labels = sorted({s["chain_label"] for s in req_sites}, key=lambda x: (len(x), x))
    for i, la in enumerate(req_labels):
        for lb in req_labels[i + 1:]:
            d = _diff(by_label[la]["signature"], by_label[lb]["signature"])
            divergence.append({"a": la, "b": lb, **d})
    res = {"concept": concept, "forms": sorted(forms), "within": within, "backend_sites": backend, "chains": chains,
           "concept_request_keys": concept_keys, "request_driven_chains": req_labels, "divergence": divergence,
           "client": [], "client_fallbacks": []}
    if client:
        res["client"], res["client_fallbacks"] = _client_side(st, req_sites, concept_keys, frontend, forms)
        res["not_forwarded"] = _not_forwarded(st, res, forms)
    return res


def _not_forwarded(st: GraphStore, res: dict, forms: set[str]) -> list[dict]:
    """Keys a client call site passes that the request it calls never sends: for the requests listed under CLIENT,
    and every gap whose dropped key is a form of the concept (e.g. date_from, which then has no backend site at all)."""
    from .query import forwarding_gaps
    issuers = {rq["issuer"] for it in res["client"] for rq in it["requests"]}
    c = res["concept"].lower()
    out = []
    for g in forwarding_gaps(st):
        concept_hit = [k for k in g["dropped"] if head_token(k) in forms or c in k.lower()]
        if g["issuer"] in issuers or concept_hit:
            out.append({**g, "concept_keys": concept_hit})
    return out


def _key_status(keys: dict | None, k: str) -> str:
    if not keys:
        return "absent"
    if keys.get("opaque") and k not in (keys.get("keys") or []) + (keys.get("conditional") or []):
        return "unknown"
    if k in (keys.get("keys") or []):
        return "always"
    if k in (keys.get("conditional") or []):
        return "conditional"
    return "absent"


def _client_side(st: GraphStore, req_sites: list[dict], concept_keys: list[str], frontend: list[dict], forms: set[str]):
    route_sites = defaultdict(list)
    for s in req_sites:
        for r in s["routes"]:
            route_sites[r].append(s)
    if not route_sites:
        return [], []
    eps = defaultdict(set)
    for r in route_sites:
        for e in st.q("SELECT src, confidence FROM edges WHERE kind='MATCHES_ROUTE' AND dst=?", (r,)):
            eps[e["src"]].add(r)
    fb_by_owner = defaultdict(list)
    for f in frontend:
        fb_by_owner[f["fn"]].append(f)
    out, used_fb = [], {}
    for ep in sorted(eps):
        routes = sorted(eps[ep])
        sites = sorted({(s["chain_label"], s["id"]) for r in routes for s in route_sites[r]})
        item = {"endpoint": ep, "routes": routes, "backend_sites": [{"chain": l, "site": i} for l, i in sites], "requests": []}
        for h in st.q("SELECT src, file, line, attrs FROM edges WHERE kind='HTTP_CALLS' AND dst=?", (ep,)):
            ha = json.loads(h["attrs"] or "{}")
            fwd = next((x for x in (ha.get("query_keys"), ha.get("body_keys")) if x and x.get("forwarded")), None) or {}
            req = {"issuer": h["src"], "at": f"{h['file']}:{h['line']}", "keys": {}, "callers": [],
                   "forwarded": fwd.get("forwarded"), "forwarded_index": fwd.get("forwarded_index")}
            for k in concept_keys:
                req["keys"][k] = max(_key_status(ha.get("query_keys"), k), _key_status(ha.get("body_keys"), k),
                                     key=["absent", "unknown", "conditional", "always"].index)
            owners = [h["src"]]
            for c in st.q("SELECT src, file, line, attrs FROM edges WHERE kind='CALLS' AND dst=?", (h["src"],)):
                ca = json.loads(c["attrs"] or "{}")
                ak = ca.get("arg_keys")
                akc = ca.get("arg_keys_conditional") or []
                akp = ca.get("arg_keys_partial") or []
                if isinstance(akp, bool):  # older DBs
                    akp = [akp] * len(ak or [])
                fi = req["forwarded_index"]
                if ak is not None and isinstance(fi, int):
                    # the issuer forwards one parameter into the request: only that argument position matters
                    one = ak[fi] if fi < len(ak) else None
                    ak = None if one is None else [one]
                    akc = [akc[fi]] if fi < len(akc) and akc[fi] else []
                    akp = [akp[fi]] if fi < len(akp) else []
                passed = sorted({x for ks in (ak or []) for x in (ks or [])})
                cond = sorted({x for ks in akc for x in (ks or [])})
                partial = any(akp)

                def sends(k):
                    if ak is None:
                        return None
                    if k in passed:
                        return True
                    if k in cond:
                        return "conditional"
                    return None if partial else False
                req["callers"].append({"caller": c["src"], "at": f"{c['file']}:{c['line']}", "arg_keys": passed if ak is not None else None,
                                       "arg_keys_conditional": cond or None, "sends": {k: sends(k) for k in concept_keys}})
                owners.append(c["src"])
            fbs = []
            for o in owners:
                for f in fb_by_owner.get(o, []):
                    fbs.append(f)
                    used_fb[f["id"]] = f
            req["client_fallbacks"] = [{"at": f"{f['file']}:{f['line']}", "expr": " ?? ".join(f["signature"][:-1]) if f["signature"] else None,
                                        "literal": f["final_literal"], "owner": f["fn"]} for f in fbs]
            # verdict per key: sent / maybe / never
            for k in concept_keys:
                st_ = req["keys"][k]
                if st_ == "always":
                    v = "sent"
                elif st_ == "absent":
                    v = "never sent (request builder has no such key)"
                elif st_ == "unknown" and not any(c["sends"].get(k) for c in req["callers"]):
                    v = "unknown (request params not statically known)"
                else:
                    vals = [c["sends"].get(k) for c in req["callers"]]
                    unknown = sum(1 for x in vals if x is None)
                    if any(x is True for x in vals):
                        v = "sent by some call sites"
                    elif any(x == "conditional" for x in vals):
                        v = "sent conditionally by some call sites"
                    elif vals and not unknown:
                        v = "never sent (builder key is conditional and no call site passes it)"
                    elif vals and unknown < len(vals):
                        v = f"not sent by {len(vals) - unknown} call site(s) with known keys; {unknown} unknown"
                    else:
                        v = "conditional (call-site keys unknown)"
                req.setdefault("verdict", {})[k] = v
            item["requests"].append(req)
        out.append(item)
    return out, sorted(used_fb.values(), key=lambda f: (f["file"], f["line"]))


def render_resolutions(res: dict, max_sites_per_chain=8, show_client=True, compact=False) -> str:
    L = [f"concept: {res['concept']} (head-word forms: {', '.join(res['forms'])})" + (f"  within: {res['within']}" if res.get("within") else ""),
         f"backend resolution sites: {len(res['backend_sites'])} in {len(res['chains'])} distinct fallback chains; "
         f"request keys of the concept: {', '.join(res['concept_request_keys']) or '-'}; "
         f"request-driven chains: {', '.join(res['request_driven_chains']) or '-'}"]
    sites = {s["id"]: s for s in res["backend_sites"]}
    L += ["", "== FALLBACK CHAINS (deterministic, grouped by signature)"]
    for c in res["chains"]:
        L.append(f"[{c['label']}] {' > '.join(c['signature'])}   ({len(c['sites'])} site{'s' if len(c['sites']) != 1 else ''})")
        for sid in c["sites"][:max_sites_per_chain]:
            s = sites[sid]
            L.append(f"    {short(s['fn'])}  {s['target']}  ({s['form']})  @{s['file']}:{s['line']}")
            i = 0
            seen_atoms = set()
            for a in flat_atoms(s["chain"]):
                if a["kind"] == "literal" and a.get("value") in (None, "", [], False):
                    continue
                ak = json.dumps({kk: vv for kk, vv in a.items() if kk in ("kind", "key", "column", "value", "text")}, sort_keys=True)
                if ak in seen_atoms:  # repeated source (e.g. `($a ?? $fb) ?: $fb`): already listed
                    continue
                seen_atoms.add(ak)
                i += 1
                L.append(f"        {i}. {atom_text(a)}  @{a.get('at')}")
            if s.get("routes"):
                L.append(f"        reached from {len(s['routes'])} route(s): {', '.join(r[6:] for r in s['routes'][:4])}{' …' if len(s['routes']) > 4 else ''}")
        if len(c["sites"]) > max_sites_per_chain:
            L.append(f"    … +{len(c['sites']) - max_sites_per_chain} more")
    if res["divergence"]:
        L += ["", f"== DIVERGENCE between request-driven chains ({', '.join(res['request_driven_chains'])})"]
        for d in res["divergence"]:
            cp = " > ".join(d["common_prefix"]) or "(nothing)"
            L.append(f"  [{d['a']}] vs [{d['b']}]: same up to {cp}; then [{d['a']}] {d['a_next'] or '(end)'} vs [{d['b']}] {d['b_next'] or '(end)'}"
                     + (f"; final fallback {d['final_a']} vs {d['final_b']}" if d["final_a"] != d["final_b"] else f"; same final fallback {d['final_a']}"))
    if show_client and res["client"]:
        L += ["", "== CLIENT (frontend endpoints reaching request-driven sites)"]
        for it in res["client"]:
            labels = sorted({b["chain"] for b in it["backend_sites"]})
            L.append(f"  {it['endpoint'][5:]}  -> chain {', '.join(labels)} via {', '.join(r[6:] for r in it['routes'][:2])}")
            if compact:  # one line per request: issuer, verdict, client fallbacks
                for rq in it["requests"]:
                    v = "; ".join(f"'{k}': {x}" for k, x in (rq.get("verdict") or {}).items())
                    fb = "".join(f"; client fallback @{f['at']}: {f['expr']} ?? {f['literal']!r}" for f in rq["client_fallbacks"][:2])
                    L.append(f"     {short(rq['issuer'])} @{rq['at']} ({len(rq['callers'])} caller(s)) => {v}{fb}")
                continue
            for rq in it["requests"]:
                keys = ", ".join(f"{k}: {v}" for k, v in rq["keys"].items()) + (f" (forwards parameter `{rq['forwarded']}`)" if rq.get("forwarded") else "")
                L.append(f"     request {short(rq['issuer'])} @{rq['at']}  builder keys: {keys}")
                for c in rq["callers"][:6]:
                    sends = ", ".join(f"{k}={'yes' if v is True else ('conditional' if v == 'conditional' else ('no' if v is False else '?'))}" for k, v in c["sends"].items())
                    L.append(f"       called by {short(c['caller'])} @{c['at']}  sends {sends}" + (f"  (passes: {', '.join(c['arg_keys'])})" if c["arg_keys"] else ""))
                for f in rq["client_fallbacks"][:4]:
                    L.append(f"       client fallback @{f['at']}: {f['expr']} ?? {f['literal']!r}")
                for k, v in (rq.get("verdict") or {}).items():
                    L.append(f"       => '{k}': {v}")
    if show_client and res.get("not_forwarded"):
        L += ["", "== SENT BUT NOT FORWARDED (a call site passes the key; the request it calls never sends it)"]
        for g in res["not_forwarded"]:
            L.append(f"  {short(g['caller'])} @{g['call_at']} passes {', '.join(g['dropped'])} to {short(g['issuer'])}; "
                     f"the request @{g['request_at']} ({g['endpoint'][5:]}) sends only {', '.join(g['request_keys'])}")
    elif show_client and not res["backend_sites"] and not res["client"]:
        L += ["", f"no resolution sites match {res['concept']!r}. try: a head word such as timezone, locale, currency or store; "
                  "search() / `cg search` for the name"]
    return "\n".join(L)
