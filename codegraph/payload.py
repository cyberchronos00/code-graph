"""Payload / field contract check between a client endpoint and the backend route it matched.

Inputs (all from the graphs, no execution):
  client  HTTP_CALLS edge attrs: body_keys [{key,type,line,file,model?}], response_models [{model,path,line}],
          response_keys [{key,path,line,cast,coalesce}], status_checks [{op,v,line}], trailing_slash;
          model class attrs json_from / json_to (key, field, type, cast, nullable, required, ref, many), enum_values.
  server  route attrs: request {schemas:[{class,location}], keys:[...]}, response {schemas:[{class,status}],
          shapes:[{status, keys: shape}]} (django/shapes.py), trailing_slash, framework, auth;
          schema class attrs schema_fields (name/json, type, nullable, required, ref, many, choices).

Issue kinds: trailing_slash, request_missing_required, request_unknown_field, request_case_mismatch,
request_type, request_nullability, request_body_missing, response_missing_key, response_case_mismatch,
response_type, response_nullability, response_enum_values, status_code. Severity: high (likely runtime
failure), medium, low, info. Every issue carries file:line on both sides when known.
"""
from __future__ import annotations

import re

DART_KIND = {"String": "str", "int": "int", "double": "float", "num": "number", "bool": "bool", "List": "list", "Iterable": "list",
             "Set": "list", "Map": "dict", "DateTime": "datetime", "dynamic": "any", "Object": "any", "Uri": "str", "Duration": "any"}
SERVER_KIND = {"str": "str", "int": "int", "float": "float", "number": "number", "decimal": "decimal", "bool": "bool", "uuid": "str",
               "datetime": "str", "date": "str", "time": "str", "duration": "any", "list": "list", "dict": "dict", "json": "any",
               "file": "str", "model": "any", "any": "any", "null": "null", "email": "str", "url": "str", "fk": "int",
               "m2m": "list", "bytes": "str", "enum": "str"}


def norm_key(k: str) -> str:
    return re.sub(r"[_\-]", "", k).lower()


def dart_kind(t: str | None, enums: set[str] | None = None) -> tuple[str | None, bool]:
    if not t:
        return None, False
    nullable = t.strip().endswith("?") or t.strip() in ("dynamic", "Object?")
    base = re.sub(r"<.*", "", t.strip().rstrip("?")).split(".")[-1]
    if enums and base in enums:
        return "enum", nullable
    return DART_KIND.get(base, "object" if base[:1].isupper() else None), nullable


def compatible(client: str | None, server: str | None, cast: str | None = None) -> bool:
    if not client or not server or client in ("any",) or server in ("any", "null"):
        return True
    if client == server:
        return True
    if client == "number" and server in ("int", "float", "decimal", "number"):
        return True
    if client == "float" and server in ("int", "number", "decimal"):
        return not (cast and cast.rstrip("?") == "double")  # `as double` on a JSON int throws on the Dart VM
    if client == "int" and server == "number":
        return True
    if client in ("str", "datetime", "enum") and server in ("str", "decimal", "enum"):
        return True
    if client == "object" and server in ("dict",):
        return True
    if client == "list" and server == "list":
        return True
    return False


class Checker:
    def __init__(self, fe_nodes: dict, be_nodes: dict, fe_name: str, be_name: str):
        self.fe, self.be = fe_nodes, be_nodes  # id -> attrs
        self.fe_name, self.be_name = fe_name, be_name
        self.enums = {nid.split("#")[-1] for nid, a in fe_nodes.items() if a.get("enum_values")}

    def at(self, side: str, file, line) -> str | None:
        if not file:
            return None
        return f"{self.fe_name if side == 'fe' else self.be_name}/{file}:{line}"

    # ---------------------------------------------------------------- server shapes
    def schema_shape(self, qual: str, depth=0) -> dict:
        a = self.be.get(f"class:{qual}") or {}
        out = {}
        for fd in a.get("schema_fields") or []:
            ch = self.schema_shape(fd["ref"], depth + 1) if fd.get("ref") and depth < 5 else None
            out[fd.get("json") or fd["name"]] = {"type": fd.get("type"), "nullable": fd.get("nullable"), "required": fd.get("required"),
                                                 "many": fd.get("many"), "children": ch, "line": fd.get("line"), "file": fd.get("file"),
                                                 "choices": fd.get("choices"), "read_only": fd.get("read_only"), "schema": qual,
                                                 "default": fd.get("has_default") or fd.get("default")}
        return out

    def success_shapes(self, ra: dict, errors: bool = False) -> list[dict]:
        resp = ra.get("response") or {}
        out = []
        for s in resp.get("shapes") or []:
            st = s.get("status")
            if errors or st is None or (isinstance(st, int) and 200 <= st < 300):
                out.append({"keys": s["keys"], "file": s.get("file"), "line": s.get("line"), "status": st})
        for sc in resp.get("schemas") or []:
            st = sc.get("status")
            if errors or st is None or (isinstance(st, int) and 200 <= st < 300):
                sh = self.schema_shape(sc["class"])
                if sc.get("many"):
                    sh = {"[]": {"type": "list", "many": True, "children": sh}}
                out.append({"keys": sh, "file": None, "line": None, "status": st, "schema": sc["class"]})
        return out

    @staticmethod
    def at_path(shape: dict, path: str | None):
        if not path:
            return shape
        cur = shape
        for part in path.split("."):
            many = part.endswith("[]")
            part = part[:-2] if many else part
            if part:
                v = cur.get(part) if isinstance(cur, dict) else None
                if not v:
                    return None
                cur = v.get("children")
            elif "[]" in cur:
                cur = cur["[]"].get("children")
            if cur is None:
                return None
        return cur

    # ---------------------------------------------------------------- checks
    def check(self, ep: str, route_id: str, ra: dict, call: dict) -> list[dict]:
        issues = []
        base = {"endpoint": ep, "route": route_id, "client_at": call.get("at"), "server_at": self.at("be", ra.get("_file"), ra.get("_line"))}

        def add(kind, sev, msg, **kw):
            issues.append({**base, "kind": kind, "severity": sev, "message": msg, **kw})

        # trailing slash
        cts, rts = call.get("trailing_slash"), ra.get("trailing_slash")
        if cts is not None and rts is not None and bool(cts) != bool(rts):
            method = ra.get("method", "")
            fw = ra.get("framework")
            sev = "high" if fw == "ninja" or method not in ("GET", "HEAD") else "medium"
            add("trailing_slash", sev, f"client URL {'has' if cts else 'lacks'} a trailing slash, route `{ra.get('uri')}` "
                f"{'has' if rts else 'does not'}; "
                + ("django-ninja does not redirect: 404" if fw == "ninja" else
                   "APPEND_SLASH cannot redirect a " + method + " (RuntimeError in DEBUG, 404/405 otherwise)" if method not in ("GET", "HEAD")
                   else "GET is redirected (301) by APPEND_SLASH"), url=call.get("url"))
        issues += self.check_request(ra, call, add)
        issues_resp = self.check_response(ra, call, add)
        issues += issues_resp
        # status codes
        statuses = {s.get("status") for s in (ra.get("response") or {}).get("shapes") or []} | \
                   {s.get("status") for s in (ra.get("response") or {}).get("schemas") or []}
        ok = {s for s in statuses if isinstance(s, int) and 200 <= s < 300}
        for sc in call.get("status_checks") or []:
            if sc.get("v") == 200 and sc.get("op") in ("==", "!=") and ok and 200 not in ok and None not in statuses:
                add("status_code", "high", f"client treats status {sc['op']} 200 as {'success' if sc['op'] == '==' else 'failure'}, "
                    f"server success responses use {sorted(ok)}", client_check=self.at("fe", sc.get("file"), sc.get("line")))
        return issues

    def check_request(self, ra, call, add):
        req = ra.get("request") or {}
        fields = {}
        for sc in req.get("schemas") or []:
            if sc.get("location") in ("body", "form", None):
                for k, v in self.schema_shape(sc["class"]).items():
                    if not v.get("read_only"):
                        fields[k] = dict(v, partial=sc.get("partial"))
        for k in req.get("keys") or []:
            if k.get("location") in ("body", "form"):
                fields.setdefault(k["name"], {"type": None, "required": not k.get("optional"), "line": k.get("line"), "file": k.get("file")})
        body = call.get("body_keys") or []
        if not fields:
            return []
        if not body:
            req_fields = [k for k, v in fields.items() if v.get("required") and not v.get("partial")]
            if req_fields and not call.get("body_opaque"):
                add("request_body_missing", "high", f"client sends no JSON body; server requires {req_fields}",
                    server_field=self.at("be", fields[req_fields[0]].get("file"), fields[req_fields[0]].get("line")))
            return []
        ck = {b["key"]: b for b in body}
        out_n = 0
        for k, v in fields.items():
            if k in ck:
                b = ck[k]
                kind, nullable = dart_kind(b.get("type"), self.enums)
                sk = SERVER_KIND.get(v.get("type") or "", None)
                if not compatible(kind, sk):
                    add("request_type", "medium", f"`{k}`: client sends {b.get('type')}, server expects {v.get('type')}",
                        key=k, client_field=self.at("fe", b.get("file"), b.get("line")), server_field=self.at("be", v.get("file"), v.get("line")))
                if nullable and v.get("nullable") is False:
                    sev = "medium" if b.get("conditional") else "high"  # unconditional key: an explicit null reaches validation
                    add("request_nullability", sev, f"`{k}`: client value is nullable ({b.get('type')}), server field is not nullable"
                        + ("" if b.get("conditional") else " -> a null value fails validation (422)"),
                        key=k, client_field=self.at("fe", b.get("file"), b.get("line")), server_field=self.at("be", v.get("file"), v.get("line")))
                continue
            close = [c for c in ck if norm_key(c) == norm_key(k)]
            if close:
                add("request_case_mismatch", "high", f"client sends `{close[0]}`, server field is `{k}`", key=k,
                    client_field=self.at("fe", ck[close[0]].get("file"), ck[close[0]].get("line")),
                    server_field=self.at("be", v.get("file"), v.get("line")))
            elif v.get("required") and not v.get("partial") and not v.get("default"):
                add("request_missing_required", "high", f"server requires `{k}` ({v.get('type')}); client body has {sorted(ck)}", key=k,
                    client_field=self.at("fe", body[0].get("file"), body[0].get("line")), server_field=self.at("be", v.get("file"), v.get("line")))
        for c, b in ck.items():
            if c not in fields and not any(norm_key(c) == norm_key(k) for k in fields):
                add("request_unknown_field", "medium", f"client sends `{c}`, which the server schema does not declare (ignored/dropped)",
                    key=c, client_field=self.at("fe", b.get("file"), b.get("line")))
        return []

    def check_response(self, ra, call, add):
        shapes = self.success_shapes(ra)
        if not shapes:
            return []
        merged: dict = {}
        for s in shapes:
            for k, v in (s["keys"] or {}).items():
                if k not in merged:
                    merged[k] = v
                elif v.get("children") and not merged[k].get("children"):
                    merged[k] = v
        # top-level keys read directly
        for rk in call.get("response_keys") or []:
            sh = self.at_path(merged, rk.get("path"))
            if sh is None:
                continue
            if rk["key"] not in sh:
                close = [k for k in sh if norm_key(k) == norm_key(rk["key"])]
                if close:
                    add("response_case_mismatch", "high", f"client reads `{rk['key']}`, server sends `{close[0]}`", key=rk["key"],
                        client_field=self.at("fe", rk.get("file"), rk.get("line")))
                else:
                    add("response_missing_key", "medium" if rk.get("coalesce") else "high",
                        f"client reads `{rk['key']}`{' at ' + rk['path'] if rk.get('path') else ''}; no success response of the route has it "
                        f"(server keys: {sorted(sh)[:12]})", key=rk["key"], client_field=self.at("fe", rk.get("file"), rk.get("line")),
                        server_shape=[self.at("be", s.get("file"), s.get("line")) for s in shapes if s.get("file")][:4])
        by_model: dict[str, list] = {}
        for rm in call.get("response_models") or []:
            if rm.get("path") not in [x.get("path") for x in by_model.get(rm["model"], [])]:
                by_model.setdefault(rm["model"], []).append(rm)
        err_merged: dict = {}
        for s in self.success_shapes(ra, errors=True):
            for k, v in (s["keys"] or {}).items():
                err_merged.setdefault(k, v)
        for mid, rms in by_model.items():
            ma = self.fe.get(mid) or {}
            if not ma.get("json_from"):
                continue
            if re.search(r"(Error|Failure|Exception|Problem)", mid.split("#")[-1]):
                merged_ = err_merged  # error envelopes are parsed on non-2xx / success=false branches
            else:
                merged_ = merged
            # the same model parsed at several JSON paths (`body['x']` with a fallback to `body`): judge the best-fitting one
            best = None
            for rm in rms:
                path = rm.get("path")
                sh = self.at_path(merged_, path) if path is not None else None
                how = "path"
                if sh is None and path is None:
                    cands = [("", merged_)] + [(k, v.get("children")) for k, v in merged_.items() if v.get("children")]
                    bo, pick = 0, None
                    for p_, c_ in cands:
                        o = len({x["key"] for x in ma["json_from"]} & set(c_ or {}))
                        if o > bo:
                            bo, pick = o, (p_, c_)
                    if pick is None:
                        continue
                    path, sh, how = pick[0], pick[1], "best-overlap"
                if sh is None:
                    continue
                tmp = []
                self.compare_model(mid, ma, sh, path, lambda kind, sev, msg, **kw: tmp.append((kind, sev, msg, kw)), how, depth=0)
                score = (sum(1 for t in tmp if t[1] == "high"), len(tmp))
                if best is None or score < best[0]:
                    best = (score, tmp, path)
            if best:
                alt = [r.get("path") for r in rms if r.get("path") != best[2]]
                for kind, sev, msg, kw in best[1]:
                    add(kind, sev, msg, **kw, **({"other_paths_parsed": alt} if alt else {}))
        return []

    def compare_model(self, mid, ma, sh, path, add, how, depth):
        mname = mid.split("#")[-1]
        for f in ma.get("json_from") or []:
            k = f["key"]
            loc = {"model": mname, "path": path or "", "key": k, "client_field": self.at("fe", f.get("file"), f.get("line")), "match": how}
            v = sh.get(k)
            if v is None:
                close = [x for x in sh if norm_key(x) == norm_key(k)]
                if close:
                    add("response_case_mismatch", "high", f"{mname}.fromJson reads `{k}`, server sends `{close[0]}`",
                        server_field=self.at("be", sh[close[0]].get("file"), sh[close[0]].get("line")), **loc)
                else:
                    tolerant = f.get("nullable") or f.get("coalesce") or f.get("default")
                    add("response_missing_key", "low" if tolerant else "high",
                        f"{mname}.fromJson reads `{k}` ({f.get('cast') or f.get('type')}); the server response{' at ' + path if path else ''} "
                        f"has no such key" + (" (client tolerates null)" if tolerant else " -> cast of null fails"), **loc)
                continue
            sat = self.at("be", v.get("file"), v.get("line"))
            ck, cnull = dart_kind(f.get("cast") or f.get("type"), self.enums)
            if f.get("cast") is None and f.get("type"):
                ck, cnull = dart_kind(f.get("type"), self.enums)
            sk = SERVER_KIND.get(v.get("type") or "", None)
            if ck == "object" and f.get("ref") and v.get("children") and depth < 4:
                ra = self.fe.get(f["ref"]) or {}
                self.compare_model(f["ref"], ra, v["children"], (path + "." if path else "") + k + ("[]" if f.get("many") else ""),
                                   add, how, depth + 1)
                continue
            if f.get("many") and f.get("ref") and v.get("children") and depth < 4:
                ra = self.fe.get(f["ref"]) or {}
                self.compare_model(f["ref"], ra, v["children"], (path + "." if path else "") + k + "[]", add, how, depth + 1)
                continue
            if not compatible(ck if not f.get("many") else "list", sk, f.get("cast")):
                add("response_type", "medium" if v.get("type") in ("fk", "model") or v.get("type") is None else "high", f"{mname}.`{k}`: client expects {f.get('cast') or f.get('type')}, server sends {v.get('type')}",
                    server_field=sat, **loc)
            snull = bool(v.get("nullable")) or v.get("type") == "null"
            if snull and f.get("nullable") is False and not f.get("coalesce") and not f.get("default"):
                add("response_nullability", "high", f"{mname}.`{k}`: server may send null, client reads it as non-nullable "
                    f"{f.get('cast') or f.get('type')}", server_field=sat, **loc)
            # enum values
            if ck == "enum" and v.get("choices"):
                en = re.sub(r"<.*", "", (f.get("type") or "").rstrip("?")).split(".")[-1]
                ev = next((a.get("enum_values") for nid, a in self.fe.items() if nid.endswith("#" + en) and a.get("enum_values")), None)
                if ev:
                    cvals = {e["wire"] for e in ev}
                    svals = {c if not isinstance(c, (list, tuple)) else c[0] for c in v["choices"]}
                    if cvals != svals:
                        add("response_enum_values", "high" if svals - cvals else "low",
                            f"{mname}.`{k}` enum {en}: client values {sorted(cvals)}, server choices {sorted(map(str, svals))}",
                            server_field=sat, **loc)
