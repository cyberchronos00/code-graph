"""Laravel / PHPUnit / Pest tests as graph nodes.

* every PHPUnit test method (`test*`, `@test`, #[Test]) of a concrete class under tests/ -> node
  `test:<Class>::<method>` (kind test) with TEST_CALLS -> the method (and -> setUp());
* every Pest `test('...')` / `it('...')` (inside `describe(...)` blocks too) -> node `test:<file>::<description>`;
  the calls made inside its closure are re-attributed from the file to the test node, and the test gets
  TEST_CALLS -> the file (beforeEach, uses(...) and other file-level setup);
* HTTP test calls (`$this->getJson('/api/x')`, `->actingAs($u)->post(route('x.store'))`, Pest `get('/x')`,
  `$this->json('PATCH', '/x')`, and the same calls through project helpers such as `$this->getAs('/x')` whose URL /
  verb are parameters) -> TEST_HTTP -> the matching route (URIs matched like cross-repo links, with the
  `/api` prefix Laravel adds to routes/api.php; `route('name')` by route name).

Edges made by test code are retyped to non-propagating TEST_* kinds afterwards (cg_code_graph/tests_index.py), so
tests never change blast radius, caller counts or entry tagging. `cg tests <symbol|route>` walks them on purpose.
"""
from __future__ import annotations

import re
from collections import defaultdict

from ...core.model import EXACT, HEURISTIC, RESOLVED
from ..php.plugin import PhpFunc, is_test_path, module_of
from ..php.strings import StrEval

HTTP_VERBS = {"get": "GET", "getjson": "GET", "post": "POST", "postjson": "POST", "put": "PUT", "putjson": "PUT",
              "patch": "PATCH", "patchjson": "PATCH", "delete": "DELETE", "deletejson": "DELETE", "options": "OPTIONS",
              "optionsjson": "OPTIONS", "head": "HEAD"}
GENERIC_VERB = {"json", "call"}   # ($method, $uri, ...)
PEST_TEST_FUNCS = {"test", "it"}


def _base(d):
    while d and d.get("k") in ("mcall", "prop"):
        d = d.get("of")
    return d or {}


def suite_of(path: str) -> str:
    m = re.search(r"(?:^|/)(?:tests|Tests)/([^/]+)/", path)
    return m.group(1) if m else "tests"


class LaravelTests:
    def __init__(self, lp, prog):
        self.lp, self.prog = lp, prog
        self.sev = StrEval(prog)
        self.calls: list[tuple] = []
        self.helpers: dict = defaultdict(dict)   # lower method name -> {fn id: {fn, verb | vi (verb param), ui (url param)}}
        self.maybe_helper: list[tuple] = []

    # ---- test-code fact handler (runs on tests/ only)
    def handle_fact(self, prog, fn: PhpFunc, f: dict, ctx) -> None:
        if f.get("t") != "call":
            return
        kind = f["kind"]
        m = (f.get("m") if kind == "method" else (f.get("n") or "").split("\\")[-1] if kind == "func" else None) or ""
        ml = m.lower()
        if kind == "method":
            base = _base(f.get("recv"))
            if base.get("k") != "this" and not (base.get("k") == "func" and (base.get("n") or "").split("\\")[-1].lower() in ("actingas", "withheaders", "withoutmiddleware")):
                return
        elif kind != "func":
            return
        args = f.get("args") or []
        if ml == "artisan" and kind == "method" and args and args[0].get("k") == "str":
            # $this->artisan('orders:prune --days=1'): the command runs in process (#60)
            lb = self.lp.b
            lb.add_edge(fn.id, lb.add_node("command", args[0]["v"].split(" ")[0]), "DISPATCHES", fn.file, f["line"],
                        EXACT, via="$this->artisan")
            self.lp.stats["test_artisan_calls"] += 1
            return
        if ml not in HTTP_VERBS and ml not in GENERIC_VERB:
            self.maybe_helper.append((fn, ml, f))      # $this->getAs('/x') through a project test helper
            return
        args = f.get("args") or []
        if ml in GENERIC_VERB:
            if len(args) < 2:
                return
            vd, ua = args[0], args[1]
            mv = self.sev.eval(vd, fn)
            verb = mv[0].upper() if mv and "{" not in mv[0] else None
        else:
            vd, (verb, ua) = None, (HTTP_VERBS[ml], args[0] if args else None)
        if not ua:
            return
        ui, vi = self._param_idx(fn, ua), (self._param_idx(fn, vd) if vd is not None and not verb else None)
        if ui is not None and (verb or vi is not None):
            # a helper whose URL (and maybe verb) is a parameter: expanded at its call sites in contribute()
            self.helpers[fn.name.lower()][fn.id] = {"fn": fn, "verb": verb, "vi": vi, "ui": ui}
            return
        if verb:
            self._record(fn, verb, ua, f["line"], m)

    @staticmethod
    def _param_idx(fn: PhpFunc, d) -> int | None:
        """Index of the parameter `d` is (a bare `$param` never reassigned in fn), else None."""
        if not d or d.get("k") != "var":
            return None
        names = [p["name"] for p in fn.params]
        if d.get("n") not in names or any(x.get("t") == "assign" and x.get("var") == d["n"] for x in fn.facts):
            return None
        return names.index(d["n"])

    def _record(self, fn: PhpFunc, verb: str, ua: dict, line: int, via: str, bind=None) -> None:
        if ua.get("k") == "func" and (ua.get("n") or "").split("\\")[-1].lower() == "route" and ua.get("args"):
            rn = self.sev.eval(ua["args"][0], fn)
            if rn and "{" not in rn[0]:
                self.calls.append((fn, verb, None, rn[0], line, via))
            return
        if ua.get("k") == "func" and (ua.get("n") or "").split("\\")[-1].lower() == "url" and ua.get("args"):
            ua = ua["args"][0]
        for p in self.sev.eval(ua, fn)[:3]:
            p = re.sub(r"^[a-z]+://[^/]+", "", p.split("?")[0].split("#")[0])
            if not p or (not p.startswith("/") and "/" not in p and not p.startswith("{")):
                continue   # `$this->get('key')` on something that is not an HTTP test client
            self.calls.append((fn, verb, "/" + p.lstrip("/"), None, line, via))

    def _expand_helpers(self, st) -> None:
        """Calls through test helpers (`$this->getAs('/x')` -> jsonAs('get', $url) -> $this->json($method, $uri)):
        a call passing a literal verb / URL is recorded; one passing its own parameters makes the caller a helper too."""
        done = set()
        for _ in range(4):
            grew = False
            for fn, ml, f in self.maybe_helper:
                hs = self.helpers.get(ml)
                key = (fn.id, f["line"], ml)
                if not hs or len(hs) != 1 or key in done:
                    continue
                h = next(iter(hs.values()))
                if h["fn"].id == fn.id:
                    continue
                args = f.get("args") or []
                ua = args[h["ui"]] if h["ui"] < len(args) else None
                vd = args[h["vi"]] if h["vi"] is not None and h["vi"] < len(args) else None
                if not ua:
                    continue
                verb = h["verb"]
                if not verb and vd is not None:
                    mv = self.sev.eval(vd, fn)
                    verb = mv[0].upper() if mv and "{" not in mv[0] else None
                ui = self._param_idx(fn, ua)
                vi = self._param_idx(fn, vd) if (vd is not None and not verb) else None
                done.add(key)
                if ui is not None and (verb or vi is not None):
                    if fn.id not in self.helpers[fn.name.lower()]:
                        self.helpers[fn.name.lower()][fn.id] = {"fn": fn, "verb": verb, "vi": vi, "ui": ui}
                        grew = True
                    continue
                if verb:
                    self._record(fn, verb, ua, f["line"], f"{ml}()")
                    st["http_calls_via_helper"] += 1
            if not grew:
                break

    # ---- after reference emission
    def contribute(self) -> dict:
        from ...link import match_endpoint
        b, prog = self.lp.b, self.prog
        st = defaultdict(int)
        self._expand_helpers(st)
        routes, by_name = [], defaultdict(list)
        for nid, n in b.nodes.items():
            if n.kind != "route" or not n.attrs.get("uri") or not n.attrs.get("method"):
                continue
            uris = [("as-declared", n.attrs["uri"])]
            if (n.file or "").startswith(("routes/api.php", "routes/api/")):
                uris.append(("api-prefixed", "/api" + n.attrs["uri"]))
            routes.append({"id": nid, "uri": n.attrs["uri"], "method": n.attrs["method"], "uris": uris})
            if n.attrs.get("name"):
                by_name[n.attrs["name"]].append(n)
        for fn, verb, path, rname, line, via in self.calls:
            st["http_calls"] += 1
            if rname:
                hits = [n for n in by_name.get(rname, []) if n.attrs.get("method") in (verb, "ANY") or (verb == "HEAD" and n.attrs.get("method") == "GET")]
                # one name on several routes (unnamed routes inside a named group carry just the group prefix): Laravel
                # resolves it to one of them, cg cannot say which
                conf = EXACT if len(hits) == 1 else HEURISTIC
                if len(hits) > 3:
                    hits = []
                    st["http_calls_ambiguous_name"] += 1
                for n in hits:
                    b.add_edge(fn.id, n.id, "TEST_HTTP", fn.file, line, conf, via=f"{via}(route('{rname}'))")
                st["http_calls_matched" if hits else "http_calls_unmatched"] += 1
                continue
            if re.fullmatch(r"/\{[^}]*\}", path):
                st["http_calls_url_unknown"] += 1     # `$this->post($url)` with $url from elsewhere: no route to name
                continue
            res = match_endpoint(verb, path, routes, "api")
            if len(res["matched"]) > 3 and all(mm["confidence"] == HEURISTIC for mm in res["matched"]):
                res["matched"] = []                   # a bare suffix shared by many routes says nothing
            for mm in res["matched"]:
                b.add_edge(fn.id, mm["route"], "TEST_HTTP", fn.file, line, mm["confidence"], via=via, path=path)
            st["http_calls_matched" if res["matched"] else "http_calls_unmatched"] += 1
        st["phpunit_tests"] = self._phpunit()
        st["pest_tests"] = self._pest()
        return dict(st)

    def _is_test_method(self, m: PhpFunc) -> bool:
        if m.abstract or m.visibility != "public" or m.static:
            return False
        if m.name.startswith("test") or (m.doc and re.search(r"@test\b", m.doc)):
            return True
        return any(a.lstrip("\\").endswith("Attributes\\Test") or a == "Test" for a in m.attributes)

    def _phpunit(self) -> int:
        b, prog = self.lp.b, self.prog
        n = 0
        for fq in sorted(prog.test_classes):
            c = prog.cls(fq)
            if not c or c.kind != "class" or c.abstract:
                continue
            seen = set()
            for a in prog.ancestors(fq):
                ac = prog.cls(a)
                if not ac or a not in prog.test_classes:
                    continue
                for ln, m in ac.methods.items():
                    if ln in seen or not self._is_test_method(m):
                        continue
                    seen.add(ln)
                    short = fq.split("\\")[-1]
                    tid = b.add_node("test", f"{fq}::{m.name}", name=f"{short}::{m.name}", fqn=f"{fq}::{m.name}", file=m.file,
                                     line=m.line, end_line=m.end_line, module=module_of(m.file), lang="php",
                                     attrs={"framework": "phpunit", "suite": suite_of(m.file), "test": True})
                    b.add_edge(tid, m.id, "TEST_CALLS", m.file, m.line, EXACT, via="test method")
                    su = prog.find_method(fq, "setUp")
                    if su and su.cls in prog.test_classes:
                        b.add_edge(tid, su.id, "TEST_CALLS", su.file, su.line, RESOLVED, via="setUp")
                    n += 1
        return n

    def _pest(self) -> int:
        b, prog = self.lp.b, self.prog
        moves = defaultdict(list)
        n = 0
        for file, sf in prog.scripts.items():
            if not is_test_path(file):
                continue
            calls = [f for f in sf.facts if f.get("t") == "call" and f["kind"] == "func" and f.get("args")]
            describes = [(f["line"], f.get("end", f["line"]), self.sev.eval(f["args"][0], sf)[0]) for f in calls
                         if (f.get("n") or "").split("\\")[-1].lower() == "describe"]
            used = defaultdict(int)
            for f in calls:
                fname = (f.get("n") or "").split("\\")[-1].lower()
                if fname not in PEST_TEST_FUNCS:
                    continue
                lo, hi = f["line"], f.get("end", f["line"])
                desc = self.sev.eval(f["args"][0], sf)[0]
                if fname == "it":
                    desc = "it " + desc
                path = [d for (dl, dh, d) in sorted(describes) if dl < lo and hi <= dh] + [desc]
                name = " > ".join(path)
                used[name] += 1
                key = f"{file}::{name}" + (f" #{used[name]}" if used[name] > 1 else "")
                tid = b.add_node("test", key, name=name, fqn=key, file=file, line=lo, end_line=hi, module=module_of(file),
                                 lang="php", attrs={"framework": "pest", "suite": suite_of(file), "test": True})
                b.add_edge(tid, sf.id, "TEST_CALLS", file, lo, RESOLVED, via="file setup (beforeEach / uses)")
                moves[sf.id].append((lo, hi, tid))
                n += 1
        if moves:
            b.move_edges(moves)
        return n
