"""Laravel framework plugin (on top of the PHP plugin). Purely static: never boots the app.

register_hooks(prog)  (before PHP reference resolution)
  * parses config/*.php (config keys, env reads, DB connections), migrations (tables/columns),
    Eloquent models (table, $connection, relations) and middleware aliases
  * registers type rules: Eloquent static forwarding -> builder:<Model>, builder/collection
    chains, relation properties, DB::table() -> qb:<table>, app()/resolve() container lookups
  * registers fact handlers: column/table reads+writes, DB connections, config()/env(),
    Config::set('database.connections.*'), dispatch/event, container bindings, Artisan::call
contribute(...)       (after)
  * routes, artisan commands + scheduler, jobs, listeners, Filament admin, observers, bindings
"""
from __future__ import annotations

import re
from collections import defaultdict

from ...core.model import EXACT, HEURISTIC, RESOLVED
from ...core.redact import looks_secret, redact
from ...core.plugin import FrameworkPlugin, GraphBuilder, Project
from ..php.plugin import PhpFunc, PhpProgram, ResolveCtx, module_of

MODEL_BASES = ("Illuminate\\Database\\Eloquent\\Model", "Illuminate\\Foundation\\Auth\\User",
               "Illuminate\\Database\\Eloquent\\Relations\\Pivot", "Illuminate\\Database\\Eloquent\\Relations\\MorphPivot")
RELATIONS = {"hasone", "hasmany", "belongsto", "belongstomany", "morphto", "morphone", "morphmany", "morphtomany",
             "morphedbymany", "hasonethrough", "hasmanythrough"}
MANY_RELATIONS = {"hasmany", "belongstomany", "morphmany", "morphtomany", "morphedbymany", "hasmanythrough"}
RETRIEVE_ONE = {"find", "findorfail", "first", "firstorfail", "firstwhere", "sole", "create", "forcecreate", "make",
                "firstorcreate", "firstornew", "updateorcreate", "findornew", "findorcreate", "newmodelinstance", "replicate", "fresh"}
RETRIEVE_MANY = {"get", "all", "paginate", "simplepaginate", "cursorpaginate", "cursor", "lazy", "lazybyid", "findmany"}
SCALAR_TERMINALS = {"count", "exists", "doesntexist", "sum", "avg", "max", "min", "pluck", "value", "delete", "update",
                    "insert", "insertgetid", "increment", "decrement", "chunk", "chunkbyid", "each", "toarray", "tosql",
                    "upsert", "forcedelete", "restore", "truncate", "implode"}
COL_ARG0 = {"where", "orwhere", "wherein", "wherenotin", "orwherein", "orwherenotin", "wherenull", "wherenotnull",
            "orwherenull", "orwherenotnull", "wherebetween", "wherenotbetween", "wheredate", "wheremonth", "whereyear",
            "whereday", "wheretime", "wherecolumn", "orderby", "orderbydesc", "groupby", "pluck", "value", "sum", "avg",
            "max", "min", "increment", "decrement", "firstwhere", "latest", "oldest", "wherejsoncontains", "wherenot",
            "wherelike", "having", "keyby", "sortby", "sortbydesc", "wherejsonlength"}
COL_ALL_ARGS = {"select", "addselect", "groupby", "only", "except"}
WRITE_ARRAY0 = {"create", "forcecreate", "insert", "insertgetid", "insertorignore", "update", "upsert", "fill", "forcefill"}
WRITE_ARRAY1 = {"firstorcreate", "updateorcreate", "updateorinsert", "firstornew"}
WRITE_TABLE = {"save", "update", "delete", "forcedelete", "insert", "insertgetid", "insertorignore", "upsert", "create",
               "forcecreate", "increment", "decrement", "truncate", "updateorcreate", "firstorcreate", "updateorinsert",
               "restore", "push", "touch", "savequietly", "updatequietly", "deletequietly"}
# belongsToMany relation builders: these write the pivot, not the related model's table
PIVOT_WRITES = {"attach", "detach", "sync", "syncwithoutdetaching", "toggle", "updateexistingpivot"}
COLLECTION_KEEP = {"filter", "where", "wherein", "sortby", "sortbydesc", "values", "keyby", "unique", "reject", "take",
                   "slice", "merge", "concat", "reverse", "wherenotnull", "wherenotin", "groupby"}
COLUMN_METHODS = set("""bigincrements biginteger binary boolean char date datetime datetimetz decimal double enum float
foreignid foreignuuid foreignulid geometry increments integer ipaddress json jsonb longtext macaddress mediumincrements
mediuminteger mediumtext point set smallincrements smallinteger string text time timetz timestamp timestamptz tinyincrements
tinyinteger tinytext unsignedbiginteger unsigneddecimal unsignedinteger unsignedmediuminteger unsignedsmallinteger
unsignedtinyinteger uuid ulid year vector""".split())
FACADES = {"db": "Illuminate\\Support\\Facades\\DB", "config": "Illuminate\\Support\\Facades\\Config",
           "schema": "Illuminate\\Support\\Facades\\Schema", "schedule": "Illuminate\\Support\\Facades\\Schedule",
           "event": "Illuminate\\Support\\Facades\\Event", "bus": "Illuminate\\Support\\Facades\\Bus",
           "artisan": "Illuminate\\Support\\Facades\\Artisan", "queue": "Illuminate\\Support\\Facades\\Queue"}
GENERIC_COLUMNS = {"created_at", "updated_at", "deleted_at", "id", "name", "status", "type", "code", "notes"}
IRREGULAR = {"person": "people", "child": "children", "man": "men", "woman": "women", "tooth": "teeth", "foot": "feet",
             "mouse": "mice", "goose": "geese", "datum": "data", "criterion": "criteria", "analysis": "analyses"}
UNCOUNTABLE = {"equipment", "information", "rice", "money", "species", "series", "fish", "sheep", "feedback", "metadata",
               "staff", "data", "audio", "media", "news", "knowledge", "traffic", "evidence"}


def snake(s: str) -> str:
    s = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", s)
    return s.lower()


def plural(word: str) -> str:
    w = word.lower()
    if w in UNCOUNTABLE:
        return word
    if w in IRREGULAR:
        return IRREGULAR[w]
    if re.search(r"(s|x|z|ch|sh)$", w):
        return word + "es"
    if re.search(r"[^aeiou]y$", w):
        return word[:-1] + "ies"
    return word + "s"


def pivot_table_name(parent_fqcn: str, related_fqcn: str) -> str:
    """Laravel's default belongsToMany pivot: the two class basenames, snake-cased, sorted, joined."""
    segs = sorted([snake(parent_fqcn.split("\\")[-1]), snake(related_fqcn.split("\\")[-1])])
    return "_".join(segs)


def model_table_by_convention(fqcn: str) -> str:
    base = fqcn.split("\\")[-1]
    words = re.findall(r"[A-Z][a-z0-9]*|[a-z0-9]+", base) or [base]
    words[-1] = plural(words[-1])
    return snake("".join(w[0].upper() + w[1:] for w in words))


def classconst(d) -> str | None:
    return d.get("class") if d and d.get("k") == "classconst" else None


_ROUTE_ATTR_VERBS = {
    "get": "GET", "post": "POST", "put": "PUT", "patch": "PATCH", "delete": "DELETE",
    "options": "OPTIONS", "head": "HEAD", "any": "ANY",
}


def _attr_short(name: str) -> str:
    return (name or "").replace("/", "\\").split("\\")[-1]


def _as_str_list(v) -> list[str]:
    if isinstance(v, str):
        return [v]
    if isinstance(v, list):
        return [x for x in v if isinstance(x, str)]
    if isinstance(v, dict):
        return [x for x in v.values() if isinstance(x, str)]
    return []


def _attr_prefixes(attrs) -> list[str]:
    out = []
    for a in attrs or []:
        if _attr_short(a.get("name") or "") != "Prefix":
            continue
        named = a.get("named") or {}
        v = named.get("prefix") if isinstance(named, dict) and "prefix" in named else (a.get("args") or [None])[0]
        if isinstance(v, str) and v.strip("/"):
            out.append(v.strip("/"))
    return out


def _attr_middleware(attrs) -> list[str]:
    out = []
    for a in attrs or []:
        if _attr_short(a.get("name") or "") != "Middleware":
            continue
        named = a.get("named") or {}
        if isinstance(named, dict) and "middleware" in named:
            out += _as_str_list(named.get("middleware"))
        elif a.get("args"):
            out += _as_str_list(a["args"][0])
    return out


def _route_attr(a: dict):
    """(methods, uri, name, middleware) for a spatie/laravel-route-attributes verb, else None."""
    short = _attr_short(a.get("name") or "")
    args = list(a.get("args") or [])
    named = a.get("named") if isinstance(a.get("named"), dict) else {}
    if short == "Route":
        method = named.get("method") if "method" in named else (args[0] if args else None)
        uri = named.get("uri") if "uri" in named else (args[1] if len(args) > 1 else None)
        methods = [m.upper() for m in _as_str_list(method)]
    elif short.lower() in _ROUTE_ATTR_VERBS:
        methods = [_ROUTE_ATTR_VERBS[short.lower()]]
        uri = named.get("uri") if "uri" in named else (args[0] if args else None)
    else:
        return None
    if not isinstance(uri, str) or not uri.strip():
        return None
    name = named.get("name") if isinstance(named.get("name"), str) else None
    if name is None and short != "Route" and len(args) > 1 and isinstance(args[1], str):
        name = args[1]
    mw = _as_str_list(named.get("middleware")) if "middleware" in named else (_as_str_list(args[2]) if len(args) > 2 else [])
    return methods, uri.strip(), name, mw


class LaravelPlugin(FrameworkPlugin):
    name = "laravel"
    language = "php"

    def detect(self, project: Project) -> bool:
        cj = project.read_json("composer.json") or {}
        return "laravel/framework" in (cj.get("require") or {}) or project.exists("artisan")

    # ------------------------------------------------------------------ setup
    def register_hooks(self, prog: PhpProgram) -> None:
        self.prog, self.b = prog, prog.b
        self.stats = defaultdict(int)
        self.observers, self.listeners, self.schedules, self.bindings = [], [], [], []
        self.seen_cols = set()
        self.records = {r["file"]: r for r in prog.records}
        self._parse_config()
        self._parse_migrations()
        self._parse_models()
        self._seed_connection_providers()
        prog.static_call_rules.append(self._rule_static)
        prog.method_call_rules.append(self._rule_method)
        prog.prop_rules.append(self._rule_prop)
        prog.func_rules.append(self._rule_func)
        prog.fact_handlers.append(self._handle_fact)
        from .broadcast import BroadcastAnalysis
        from .tests import LaravelTests
        self.broadcast = BroadcastAnalysis(self, prog)
        self.broadcast.register()          # types channel-callback users before inference
        self.tests = LaravelTests(self, prog)
        prog.test_fact_handlers.append(self.tests.handle_fact)

    # ---- config/*.php
    def _parse_config(self):
        b = self.b
        self.config_keys: dict[str, dict] = {}
        self.connections: dict[str, str] = {}  # name -> node id
        self.config_conn_refs: dict[str, str] = {}  # config key -> connection node id
        for f, r in self.records.items():
            if not f.startswith("config/") or "config" not in r:
                continue
            root = f[len("config/"):-len(".php")].replace("/", ".")
            rid = b.add_node("config", root, file=f, line=1, module="config", lang="php")

            def walk(nodes, parent_id):
                for n in nodes:
                    key = f"{root}.{n['key']}"
                    attrs = {}
                    if n.get("literal") is not None and not isinstance(n["literal"], (dict, list)):
                        attrs["value"] = redact(n["literal"], n["key"], self.b.redact_salt)
                    if n.get("envs"):
                        attrs["env"] = [e["env"] for e in n["envs"]]
                        attrs["env_default"] = [redact(e["default"], e["env"] if looks_secret(e["env"]) else n["key"], self.b.redact_salt)
                                                for e in n["envs"]]
                    cid = b.add_node("config", key, file=f, line=n["line"], module="config", lang="php", attrs=attrs)
                    self.config_keys[key] = n
                    b.add_edge(parent_id, cid, "CONFIG_CONTAINS", f, n["line"], EXACT)
                    for e in n.get("envs", []):
                        eid = b.add_node("env", e["env"], lang="env")
                        b.add_edge(cid, eid, "READS_ENV", f, e["line"], EXACT)
                    for ref in n.get("config_refs", []):
                        b.add_edge(cid, b.add_node("config", ref, lang="php"), "REFERS_TO", f, n["line"], EXACT)
                    walk(n.get("children", []), cid)
            walk(r["config"], rid)
        # DB connections
        for key, n in self.config_keys.items():
            m = re.match(r"^database\.connections\.([^.]+)$", key)
            if m:
                name = m.group(1)
                cid = self.b.add_node("connection", name, file="config/database.php", line=n["line"], module="config",
                                      lang="php", attrs={"defined_in": "config/database.php"})
                self.connections[name] = cid
                self.b.add_edge(cid, f"config:{key}", "CONFIGURED_BY", "config/database.php", n["line"], EXACT)
        # config values that name a connection (literal value or env() default)
        for key, n in self.config_keys.items():
            vals = []
            if isinstance(n.get("literal"), str):
                vals.append(n["literal"])
            vals += [e["default"] for e in n.get("envs", []) if isinstance(e.get("default"), str)]
            for v in vals:
                if v in self.connections and (key.endswith("connection") or key == "database.default" or key.endswith(".connection")):
                    self.config_conn_refs[key] = self.connections[v]
                    via = "literal" if v == n.get("literal") else "env-default"
                    self.b.add_edge(f"config:{key}", self.connections[v], "REFERS_TO", f"config/{key.split('.')[0]}.php",
                                    n["line"], RESOLVED, via=via)

    # ---- database/migrations
    def _parse_migrations(self):
        b = self.b
        self.tables: dict[str, dict] = {}  # name -> {"columns": {col: line}, "file":..}
        mig = sorted((c for c in self.prog.classes.values() if c.file.startswith("database/migrations/")), key=lambda c: c.file)
        for c in mig:
            up = c.methods.get("up")
            if not up:
                continue
            for f in up.facts:
                if f["t"] != "call":
                    continue
                ctx, m = f.get("ctx") or {}, (f.get("m") or "").lower()
                args = f.get("args") or []
                a0 = args[0] if args else None
                if f["kind"] == "static" and (f.get("class") or "").endswith("Schema"):
                    if m == "create" and a0 and a0.get("k") == "str":
                        self._table(a0["v"], c.file, f["line"], created=True)
                    elif m in ("drop", "dropifexists") and a0 and a0.get("k") == "str":
                        self.tables.pop(a0["v"], None)
                    elif m == "rename" and len(args) > 1 and a0.get("k") == "str" and args[1].get("k") == "str":
                        t = self.tables.pop(a0["v"], None)
                        if t:
                            self.tables[args[1]["v"]] = t
                    continue
                if f["kind"] != "method" or (f.get("recv") or {}).get("k") != "var":
                    continue
                if not ((ctx.get("class") or "").endswith("Schema") and ctx.get("m", "").lower() in ("create", "table") and ctx.get("arg0")):
                    continue
                t = self._table(ctx["arg0"], c.file, f["line"])
                cols = []
                if m in COLUMN_METHODS and a0 and a0.get("k") == "str":
                    cols = [a0["v"]]
                elif m == "id":
                    cols = [a0["v"]] if a0 and a0.get("k") == "str" else ["id"]
                elif m in ("timestamps", "nullabletimestamps", "timestampstz"):
                    cols = ["created_at", "updated_at"]
                elif m in ("softdeletes", "softdeletestz"):
                    cols = [a0["v"]] if a0 and a0.get("k") == "str" else ["deleted_at"]
                elif m == "remembertoken":
                    cols = ["remember_token"]
                elif m in ("morphs", "nullablemorphs", "uuidmorphs", "nullableuuidmorphs", "ulidmorphs") and a0 and a0.get("k") == "str":
                    cols = [a0["v"] + "_id", a0["v"] + "_type"]
                elif m == "foreignidfor" and a0 and a0.get("k") == "classconst":
                    cols = [args[1]["v"]] if len(args) > 1 and args[1].get("k") == "str" else [snake(a0["class"].split("\\")[-1]) + "_id"]
                elif m == "dropcolumn":
                    names = [a0["v"]] if a0 and a0.get("k") == "str" else [i["v"]["v"] for i in (a0 or {}).get("items", []) if (i.get("v") or {}).get("k") == "str"]
                    for n in names:
                        t["columns"].pop(n, None)
                elif m == "renamecolumn" and len(args) > 1 and a0.get("k") == "str" and args[1].get("k") == "str":
                    old = t["columns"].pop(a0["v"], None)
                    t["columns"][args[1]["v"]] = old or (c.file, f["line"])
                for col in cols:
                    t["columns"].setdefault(col, (c.file, f["line"]))
        col_tables = defaultdict(set)
        for tn, t in self.tables.items():
            tid = b.add_node("table", tn, file=t["file"], line=t["line"], module="database/migrations", lang="sql",
                             attrs={"migrations": sorted(t["files"])})
            for col, (cf, cl) in t["columns"].items():
                cid = b.add_node("column", f"{tn}.{col}", name=col, fqn=f"{tn}.{col}", file=cf, line=cl,
                                 module="database/migrations", lang="sql")
                b.add_edge(tid, cid, "CONTAINS", cf, cl, EXACT)
                col_tables[col].add(tn)
        self.col_tables = col_tables
        self.distinctive_cols = {c: next(iter(ts)) for c, ts in col_tables.items()
                                 if len(ts) == 1 and "_" in c and len(c) >= 10 and c not in GENERIC_COLUMNS}
        self.stats["tables"] = len(self.tables)

    def _table(self, name, file, line, created=False):
        t = self.tables.get(name)
        if t is None:
            t = self.tables[name] = {"columns": {}, "file": file, "line": line, "files": set()}
        t["files"].add(file)
        return t

    # ---- Eloquent models
    def _parse_models(self):
        prog, b = self.prog, self.b
        self.models: dict[str, dict] = {}
        for c in prog.classes.values():
            if c.kind != "class" or c.fqcn.startswith("class@anonymous") or prog.is_test_class(c.fqcn):
                continue
            if not any(prog.is_a(c.fqcn, base) for base in MODEL_BASES):
                continue
            tp = prog.find_prop(c.fqcn, "table")
            explicit = tp and isinstance(tp.get("default"), str)
            table = tp["default"] if explicit else model_table_by_convention(c.fqcn)
            cp = prog.find_prop(c.fqcn, "connection")
            conn = cp["default"] if cp and isinstance(cp.get("default"), str) else None
            self.models[c.fqcn] = {"table": table, "explicit": bool(explicit), "connection": conn, "relations": {}}
        for fq, info in self.models.items():
            c = prog.classes[fq]
            info["relations"] = self._relations_of(fq, c.methods)
            # relations declared on a trait the model uses (ownedPlaylists() on HasUserRelationships)
            for anc in prog.ancestors(fq, include_self=False):
                tc = prog.cls(anc)
                if not tc or tc.kind != "trait":
                    continue
                for name, rel in self._relations_of(fq, tc.methods).items():
                    info["relations"].setdefault(name, rel)
        self.stats["models"] = len(self.models)

    def _relations_of(self, parent_fq: str, methods: dict) -> dict:
        """Relation methods on one type. A method that returns `$this->other()->wherePivot(...)` copies `other`."""
        found: dict[str, dict] = {}
        for ln, m in methods.items():
            for f in m.facts:
                if f["t"] != "return":
                    continue
                d = f["expr"]
                while d and d.get("k") == "mcall" and not (
                        d.get("of", {}).get("k") == "this" and (d.get("m") or "").lower() in RELATIONS):
                    d = d.get("of")
                if d and d.get("k") == "mcall" and (d.get("m") or "").lower() in RELATIONS:
                    args = d.get("args") or []
                    target = classconst(args[0]) if args else None
                    kind = (d.get("m") or "").lower()
                    explicit = args[1]["v"] if kind == "belongstomany" and len(args) > 1 and args[1].get("k") == "str" else None
                    pivot = explicit or (pivot_table_name(parent_fq, target) if kind == "belongstomany" and target else None)
                    found[ln] = {"kind": d["m"], "target": target, "method": m, "line": f["line"], "pivot": pivot,
                                 "pivot_explicit": bool(explicit)}
                    break
        changed = True
        while changed:
            changed = False
            for ln, m in methods.items():
                if ln in found:
                    continue
                for f in m.facts:
                    if f["t"] != "return":
                        continue
                    d = f["expr"]
                    while d and d.get("k") == "mcall":
                        of = d.get("of") or {}
                        name = (d.get("m") or "").lower()
                        if of.get("k") == "this" and name in found:
                            src = found[name]
                            found[ln] = {**src, "method": m, "line": f["line"]}
                            changed = True
                            break
                        d = of
                    if ln in found:
                        break
        return found

    def model_of(self, t: str) -> str | None:
        return t if t in self.models else None

    def table_of(self, model: str) -> str | None:
        return self.models.get(model, {}).get("table")

    # ---- dynamic connections registered via Config::set('database.connections.{...}')
    def render(self, fn: PhpFunc, d, depth=0) -> str | None:
        if not d or depth > 5:
            return None
        k = d.get("k")
        if k == "str":
            return d["v"]
        if k == "interp":
            return "".join(self.render(fn, p, depth + 1) or "{?}" for p in d["parts"])
        if k == "concat":
            return (self.render(fn, d["l"], depth + 1) or "{?}") + (self.render(fn, d["r"], depth + 1) or "{?}")
        if k == "var":
            for f in fn.facts:
                if f["t"] == "assign" and f["var"] == d["n"] and f["expr"].get("k") in ("str", "interp", "concat"):
                    return self.render(fn, f["expr"], depth + 1)
            return "{" + d["n"] + "}"
        if k == "prop" and d.get("of", {}).get("k") == "var":
            return "{" + d["of"]["n"] + "." + (d.get("n") or "?") + "}"
        return None

    def _config_key_writes(self, fn: PhpFunc, f: dict) -> list[tuple[str, int]]:
        """Keys written by Config::set(k, v) / config([k => v])."""
        out = []
        kind, m = f["kind"], (f.get("m") or "").lower()
        args = f.get("args") or []
        if kind == "static" and (f.get("class") or "") == FACADES["config"] and m == "set" and args:
            out.append(self.render(fn, args[0]))
        if kind == "func" and (f.get("n") or "").lower() == "config" and args and args[0].get("k") == "arr":
            for it in args[0]["items"]:
                out.append(self.render(fn, it.get("key")))
        return [k for k in out if k]

    def _seed_connection_providers(self):
        self.registered: dict[str, list] = defaultdict(list)
        for fn in self.prog.all_funcs:
            for f in fn.facts:
                if f["t"] != "call":
                    continue
                for key in self._config_key_writes(fn, f):
                    m = re.match(r"^database\.connections\.(.+)$", key)
                    if m:
                        name = m.group(1)
                        cid = self.b.add_node("connection", name, lang="php", module=module_of(fn.file),
                                              attrs={"dynamic": "{" in name, "registered_by": fn.id})
                        self.connections.setdefault(name, cid)
                        self.registered[fn.id].append(cid)
                        # the function is a "connection provider": its return value names this connection
                        fn.inferred.add(f"connname:{cid}")

    # ------------------------------------------------------------------ type rules
    def _rule_static(self, prog, cls, m, args):
        if not cls or not m:
            return None
        ml = m.lower()
        if cls == FACADES["db"]:
            if ml == "table" and args and args[0].get("k") == "str":
                return {f"qb:{args[0]['v'].split(' ')[0]}"}
            if ml == "connection":
                return {"conn:?"}
            return None
        if cls in self.models:
            meth = prog.find_method(cls, m)
            base = prog.classes.get(meth.cls) if meth else None
            if meth and not (base and base.abstract) and meth.returns and ml not in ("on", "query"):
                return None  # project-defined static with declared return type wins
            if ml in RETRIEVE_ONE:
                return {cls}
            if ml in RETRIEVE_MANY:
                return {f"collection:{cls}"}
            if ml in SCALAR_TERMINALS:
                return None
            return {f"builder:{cls}"}
        return None

    def _rule_method(self, prog, t, m, args):
        if not m:
            return None
        ml = m.lower()
        if t.startswith("builder:"):
            mdl = t.split(":", 1)[1]
            if ml in RETRIEVE_ONE:
                return {mdl}
            if ml in RETRIEVE_MANY:
                return {f"collection:{mdl}"}
            if ml in SCALAR_TERMINALS:
                return None
            return {t}
        if t.startswith("collection:"):
            if ml in ("first", "last", "find", "firstwhere", "sole", "pop", "shift", "get"):
                return {t.split(":", 1)[1]}
            if ml in COLLECTION_KEEP:
                return {t}
            return None
        if t.startswith("pivot:"):
            return {t}
        if t.startswith("qb:"):
            if ml in ("get", "first", "value", "pluck", "count", "exists") or ml in SCALAR_TERMINALS:
                return None
            return {t}
        if t.startswith("conn:") and ml == "table" and args and args[0].get("k") == "str":
            return {f"qb:{args[0]['v'].split(' ')[0]}"}
        if t in self.models:
            rel = self.models[t]["relations"].get(ml)
            if rel and rel["target"]:
                out = {f"builder:{rel['target']}"}
                if rel.get("pivot"):
                    out.add(f"pivot:{rel['pivot']}")
                return out
            if ml in ("newquery", "query", "newmodelquery"):
                return {f"builder:{t}"}
            if ml in ("fresh", "replicate", "refresh"):
                return {t}
        if ml in ("make", "makewith") and args and classconst(args[0]):
            return {classconst(args[0])}
        return None

    def _rule_prop(self, prog, t, prop):
        if t in self.models and prop:
            rel = self.models[t]["relations"].get(prop.lower())
            if rel and rel["target"]:
                return {f"collection:{rel['target']}"} if rel["kind"].lower() in MANY_RELATIONS else {rel["target"]}
        return None

    def _rule_func(self, prog, name, args):
        n = (name or "").lower()
        if n in ("app", "resolve") and args and classconst(args[0]):
            return {classconst(args[0])}
        if n == "app" and not args:
            return {"Illuminate\\Foundation\\Application"}
        return None

    # ------------------------------------------------------------------ fact handlers
    def _col_edge(self, fn, table, col, kind, line, conf, **attrs):
        if not table or not col or col == "*" or not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", col):
            return
        known = table in self.tables and col in self.tables[table]["columns"]
        if not known:
            if table in self.tables:
                return  # table known but column isn't (likely alias/computed/json path)
            # table not in migrations (e.g. tables owned by another DB): infer the column
            self.b.add_node("table", table, lang="sql", attrs={"inferred": True})
            cid = self.b.add_node("column", f"{table}.{col}", name=col, fqn=f"{table}.{col}", lang="sql", attrs={"inferred": True})
            self.b.add_edge(f"table:{table}", cid, "CONTAINS", None, None, HEURISTIC)
            conf = HEURISTIC
        self.b.add_edge(fn.id, f"column:{table}.{col}", kind, fn.file, line, conf, **attrs)
        self.seen_cols.add((fn.id, line, table, col))

    def _split_col(self, s: str, default_table: str | None):
        s = s.strip()
        s = re.split(r"\s+as\s+", s, flags=re.I)[0].strip()
        if "." in s:
            t, c = s.rsplit(".", 1)
            return (t.split(" ")[0], c)
        return (default_table, s)

    def _col_args(self, fn, table, f, conf, write_methods=True):
        ml = (f.get("m") or "").lower()
        args = f.get("args") or []
        line = f.get("line")
        if ml in COL_ARG0 and args:
            a0 = args[0]
            if a0.get("k") == "str":
                t, c = self._split_col(a0["v"], table)
                kind = "WRITES_COLUMN" if ml in ("increment", "decrement") else "READS_COLUMN"
                self._col_edge(fn, t, c, kind, line, conf, via=f.get("m"))
            elif a0.get("k") == "arr" and ml in ("where", "orwhere"):
                for it in a0["items"]:
                    k = it.get("key")
                    if k and k.get("k") == "str":
                        t, c = self._split_col(k["v"], table)
                        self._col_edge(fn, t, c, "READS_COLUMN", line, conf, via=f.get("m"))
        if ml in COL_ALL_ARGS:
            for a in args:
                vals = [a["v"]] if a.get("k") == "str" else [i["v"]["v"] for i in a.get("items", []) if (i.get("v") or {}).get("k") == "str"]
                for v in vals:
                    t, c = self._split_col(v, table)
                    self._col_edge(fn, t, c, "READS_COLUMN", line, conf, via=f.get("m"))
        if write_methods:
            for idx, names in ((0, WRITE_ARRAY0), (1, WRITE_ARRAY1)):
                if ml in names and len(args) > idx and args[idx].get("k") == "arr":
                    for it in args[idx]["items"]:
                        k = it.get("key")
                        if k and k.get("k") == "str":
                            t, c = self._split_col(k["v"], table)
                            self._col_edge(fn, t, c, "WRITES_COLUMN", line, conf, via=f.get("m"))
            if ml in WRITE_ARRAY1 and args and args[0].get("k") == "arr":
                for it in args[0]["items"]:
                    k = it.get("key")
                    if k and k.get("k") == "str":
                        t, c = self._split_col(k["v"], table)
                        self._col_edge(fn, t, c, "READS_COLUMN", line, conf, via=f.get("m"))
            if ml in WRITE_TABLE and table:
                self.b.add_edge(fn.id, self._table_node(table), "WRITES_TABLE", fn.file, line, conf, via=f.get("m"))

    def _table_node(self, t):
        return self.b.add_node("table", t, lang="sql", attrs={} if t in self.tables else {"inferred": True})

    def _pivot_attr_keys(self, ml: str, args: list) -> list[str]:
        """Column names passed as pivot attributes (attach/toggle/updateExistingPivot second array, nested sync arrays)."""
        if ml in ("attach", "toggle", "updateexistingpivot") and len(args) > 1:
            src = [args[1]]
        elif ml in ("sync", "syncwithoutdetaching") and args:
            src = [args[0]]
        else:
            return []
        keys = []

        def walk(arg, nested: bool):
            if not arg or arg.get("k") != "arr":
                return
            for it in arg.get("items") or []:
                v = it.get("v") or {}
                if v.get("k") == "arr":
                    walk(v, True)
                elif nested:
                    k = it.get("key")
                    if k and k.get("k") == "str" and re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", k["v"]):
                        keys.append(k["v"])

        # attach($id, ['role' => ..]) — the array itself is the attribute map (treat as nested)
        # sync([$id => ['role' => ..]]) — only the inner maps are attributes
        if ml in ("sync", "syncwithoutdetaching"):
            walk(src[0], False)
        else:
            walk(src[0], True)
        return keys

    def _pivot_write(self, fn, table: str, f: dict, conf: str):
        ml = (f.get("m") or "").lower()
        line = f.get("line")
        self.b.add_edge(fn.id, self._table_node(table), "WRITES_TABLE", fn.file, line, conf, via=f.get("m"))
        for col in self._pivot_attr_keys(ml, f.get("args") or []):
            self._col_edge(fn, table, col, "WRITES_COLUMN", line, conf, via=f.get("m"))

    def _conn_targets(self, ctx: ResolveCtx, arg) -> list[tuple[str, str, str]]:
        """-> [(connection node id, confidence, via)] for a connection-name argument."""
        out = []
        if not arg:
            return out
        for s in ctx.strings_of(arg):
            if s in self.connections:
                out.append((self.connections[s], EXACT, "literal"))
        if arg.get("k") == "func" and (arg.get("n") or "").lower() == "config" and arg.get("args"):
            key = (arg["args"][0] or {}).get("v")
            if key in self.config_conn_refs:
                out.append((self.config_conn_refs[key], RESOLVED, f"config('{key}')"))
        for t in ctx.type_of(arg):
            if t.startswith("connname:"):
                out.append((t.split(":", 1)[1], RESOLVED, "returned-connection-name"))
        return out

    def _handle_fact(self, prog: PhpProgram, fn: PhpFunc, f: dict, ctx: ResolveCtx):
        b, t, line = self.b, f["t"], f.get("line")
        if t == "fetch":
            prop = f.get("prop")
            for rt in ctx.type_of(f.get("recv")):
                if rt in self.models and prop:
                    rel = self.models[rt]["relations"].get(prop.lower())
                    if rel:
                        b.add_edge(fn.id, rel["method"].id, "CALLS", fn.file, line, RESOLVED, via="relation-property")
                    elif not prog.find_prop(rt, prop) or prog.find_prop(rt, prop).get("src") == "doc":
                        self._col_edge(fn, self.table_of(rt), prop, "WRITES_COLUMN" if f.get("write") else "READS_COLUMN",
                                       line, RESOLVED, via="model-attribute", model=rt)
            return
        if t == "new" and f.get("class") in self.models and self.models[f["class"]]["connection"]:
            self._model_conn(fn, f["class"], line)
        if t != "call":
            return
        kind, m = f["kind"], (f.get("m") or "")
        ml = m.lower()
        args = f.get("args") or []
        if kind == "static":
            cls = f.get("class") or ""
            if cls == FACADES["db"]:
                if ml == "table" and args and args[0].get("k") == "str":
                    b.add_edge(fn.id, self._table_node(args[0]["v"].split(" ")[0]), "READS_TABLE", fn.file, line, EXACT, via="DB::table")
                if ml == "connection":
                    for cid, conf, via in self._conn_targets(ctx, args[0] if args else None):
                        b.add_edge(fn.id, cid, "USES_CONNECTION", fn.file, line, conf, via=f"DB::connection {via}")
            elif cls == FACADES["schema"] and ml == "connection":
                for cid, conf, via in self._conn_targets(ctx, args[0] if args else None):
                    b.add_edge(fn.id, cid, "USES_CONNECTION", fn.file, line, conf, via=f"Schema::connection {via}")
            elif cls == FACADES["config"]:
                if ml in ("get", "string", "integer", "boolean", "array", "float", "has") and args and args[0].get("k") == "str":
                    self._config_read(fn, args[0]["v"], line, EXACT)
            elif cls in self.models:
                tbl = self.table_of(cls)
                self._col_args(fn, tbl, f, RESOLVED)
                if ml == "on" and args:
                    for cid, conf, via in self._conn_targets(ctx, args[0]):
                        b.add_edge(fn.id, cid, "USES_CONNECTION", fn.file, line, conf, via=f"{cls.split(chr(92))[-1]}::on {via}")
                if self.models[cls]["connection"]:
                    self._model_conn(fn, cls, line)
            if ml in ("dispatch", "dispatchsync", "dispatchnow", "dispatchif", "dispatchunless", "dispatchafterresponse") and cls and prog.cls(cls):
                self._dispatch(fn, cls, line, EXACT)
            if cls == FACADES["artisan"] and ml in ("call", "queue") and args and args[0].get("k") == "str":
                b.add_edge(fn.id, b.add_node("command", args[0]["v"].split(" ")[0]), "DISPATCHES", fn.file, line, EXACT, via="Artisan::call")
            if cls in (FACADES["event"],) and ml == "listen" and args:
                self._listen(fn, args, line)
            if cls in (FACADES["event"], FACADES["bus"]) and ml in ("dispatch", "dispatchsync", "dispatchnow") and args and args[0].get("k") == "new":
                self._dispatch(fn, args[0].get("class"), line, EXACT)
            if ml == "observe" and cls in self.models and args and classconst(args[0]):
                self.observers.append((cls, classconst(args[0]), fn, line))
        elif kind == "func":
            n = (f.get("n") or "").lower()
            if n == "config" and args:
                a0 = args[0]
                if a0.get("k") == "str":
                    self._config_read(fn, a0["v"], line, EXACT)
            elif n == "env" and args and args[0].get("k") == "str":
                b.add_edge(fn.id, b.add_node("env", args[0]["v"], lang="env"), "READS_ENV", fn.file, line, EXACT)
            elif n in ("dispatch", "dispatch_sync", "dispatch_now", "event", "broadcast") and args and args[0].get("k") == "new":
                self._dispatch(fn, args[0].get("class"), line, EXACT)
        else:  # method call
            recv = f.get("recv") or {}
            types = ctx.type_of(recv)
            for rt in types:
                if rt.startswith("pivot:") and ml in PIVOT_WRITES:
                    self._pivot_write(fn, rt.split(":", 1)[1], f, RESOLVED)
                elif rt.startswith("builder:"):
                    self._col_args(fn, self.table_of(rt.split(":", 1)[1]), f, RESOLVED)
                elif rt.startswith("qb:"):
                    tbl = rt.split(":", 1)[1]
                    self._col_args(fn, tbl, f, RESOLVED)
                elif rt in self.models:
                    tbl = self.table_of(rt)
                    if ml in WRITE_TABLE or ml in WRITE_ARRAY0:
                        self._col_args(fn, tbl, {**f, "m": m}, RESOLVED)
                elif rt.startswith("conn:") and ml == "table" and args and args[0].get("k") == "str":
                    b.add_edge(fn.id, self._table_node(args[0]["v"].split(" ")[0]), "READS_TABLE", fn.file, line, RESOLVED, via="connection()->table")
            if ml == "setconnection" and args:
                for cid, conf, via in self._conn_targets(ctx, args[0]):
                    b.add_edge(fn.id, cid, "USES_CONNECTION", fn.file, line, conf, via=f"setConnection {via}")
            if ml == "connection" and args and recv.get("k") in ("func", "scall"):
                for cid, conf, via in self._conn_targets(ctx, args[0]):
                    b.add_edge(fn.id, cid, "USES_CONNECTION", fn.file, line, conf, via=f"->connection {via}")
            if ml == "get" and recv.get("k") == "func" and (recv.get("n") or "").lower() == "config" and args and args[0].get("k") == "str":
                self._config_read(fn, args[0]["v"], line, EXACT)
            if ml in ("bind", "singleton", "scoped", "instance", "bindif", "singletonif") and args and classconst(args[0]):
                self.bindings.append((fn, f, ctx))
            if ml == "artisan" and recv.get("k") == "this" and args and args[0].get("k") == "str":
                # feature tests: $this->artisan('app:prune') runs the command in process (#60)
                b.add_edge(fn.id, b.add_node("command", args[0]["v"].split(" ")[0]), "DISPATCHES", fn.file, line, EXACT, via="$this->artisan")
                self.stats["test_artisan_calls"] += 1
            if ml in ("call", "callsilently", "callsilent") and recv.get("k") == "this" and args and args[0].get("k") == "str" and ":" in args[0]["v"]:
                b.add_edge(fn.id, b.add_node("command", args[0]["v"].split(" ")[0]), "DISPATCHES", fn.file, line, EXACT, via="$this->call")
            if ml in ("command", "job") and (recv.get("k") == "var" and "Illuminate\\Console\\Scheduling\\Schedule" in types):
                self.schedules.append((fn, f))
            if ml == "dispatch" and args and args[0].get("k") == "new":
                self._dispatch(fn, args[0].get("class"), line, RESOLVED)
        # config writes / dynamic connection registration
        for key in self._config_key_writes(fn, f):
            cid = b.add_node("config", key, lang="php", attrs={"runtime_written": True})
            b.add_edge(fn.id, cid, "WRITES_CONFIG", fn.file, line, EXACT if "{" not in key else RESOLVED)
            mm = re.match(r"^database\.connections\.(.+)$", key)
            if mm and mm.group(1) in self.connections:
                b.add_edge(fn.id, self.connections[mm.group(1)], "REGISTERS_CONNECTION", fn.file, line, RESOLVED)
        # schedules in routes/console.php (facade form)
        if kind == "static" and (f.get("class") or "") == FACADES["schedule"] and ml in ("command", "job"):
            self.schedules.append((fn, f))
        # distinctive column-name literals anywhere in the call's arguments (migrations define, not use)
        for s in ([] if fn.file.startswith("database/migrations/") else self._literals(args)):
            tbl = self.distinctive_cols.get(s)
            if tbl and (fn.id, line, tbl, s) not in self.seen_cols:
                b.add_edge(fn.id, f"column:{tbl}.{s}", "MENTIONS_COLUMN", fn.file, line, HEURISTIC, via="string-literal")
                self.seen_cols.add((fn.id, line, tbl, s))

    def _literals(self, ds, depth=0):
        if depth > 4:
            return
        for d in ds or []:
            if not d:
                continue
            k = d.get("k")
            if k == "str":
                yield d["v"]
            elif k == "arr":
                for it in d["items"]:
                    if it.get("key") and it["key"].get("k") == "str":
                        yield it["key"]["v"]
                    yield from self._literals([it.get("v")], depth + 1)
            elif k == "alt":
                yield from self._literals(d["opts"], depth + 1)

    def _model_conn(self, fn, cls, line):
        name = self.models[cls]["connection"]
        cid = self.connections.get(name) or self.b.add_node("connection", name, lang="php", attrs={"undefined": True})
        self.b.add_edge(fn.id, cid, "USES_CONNECTION", fn.file, line, RESOLVED, via=f"{cls.split(chr(92))[-1]}::$connection")

    def _config_read(self, fn, key, line, conf):
        cid = self.b.add_node("config", key, lang="php")
        self.b.add_edge(fn.id, cid, "READS_CONFIG", fn.file, line, conf)

    def _dispatch(self, fn, cls, line, conf):
        c = self.prog.cls(cls)
        if not c:
            return
        h = self.prog.find_method(c.fqcn, "handle") or self.prog.find_method(c.fqcn, "__invoke")
        if self._is_job(c.fqcn) and h:
            self.b.add_edge(fn.id, h.id, "DISPATCHES", fn.file, line, conf, via="job")
        else:
            eid = self.b.add_node("event", c.fqcn, name=c.fqcn.split("\\")[-1], fqn=c.fqcn, file=c.file, line=c.line,
                                  module=module_of(c.file), lang="php")
            self.b.add_edge(fn.id, eid, "DISPATCHES", fn.file, line, conf, via="event")

    def _listen(self, fn, args, line):
        ev = classconst(args[0])
        if not ev or len(args) < 2:
            return
        l = args[1]
        target = None
        if classconst(l):
            target = self.prog.find_method(classconst(l), "handle") if self.prog.cls(classconst(l)) else None
        elif l.get("k") == "arr" and len(l["items"]) == 2:
            c, m = classconst(l["items"][0].get("v")), (l["items"][1].get("v") or {}).get("v")
            target = self.prog.find_method(c, m) if c and self.prog.cls(c) else None
        eid = self.b.add_node("event", ev, name=ev.split("\\")[-1], fqn=ev, lang="php")
        if target:
            self.listeners.append((eid, target, fn, line))

    def _is_job(self, fqcn):
        return self.prog.is_a(fqcn, "Illuminate\\Contracts\\Queue\\ShouldQueue") or fqcn.startswith("App\\Jobs\\")

    # ------------------------------------------------------------------ contribute
    def contribute(self, project: Project, builder: GraphBuilder, prog: PhpProgram) -> dict:
        b = builder
        self._models_out()
        self._routes_out()
        self._commands_out()
        self._schedules_out()
        self._jobs_listeners_admin_out()
        self._bindings_out()
        from .values import ValueAnalysis
        import time as _t
        t0 = _t.time()
        vst = ValueAnalysis(self, prog, b).run()
        vst["seconds"] = round(_t.time() - t0, 2)
        # after every edge of the files is emitted: channel callbacks / Pest tests re-attribute edges by line range
        self.stats["broadcast"] = self.broadcast.contribute()
        self.stats["tests"] = self.tests.contribute()
        st = dict(self.stats)
        st["values"] = vst
        st.update({"connections": len(self.connections), "config_keys": len(self.config_keys)})
        return st

    def _models_out(self):
        b = self.b
        for fq, info in self.models.items():
            c = self.prog.classes[fq]
            cid = f"class:{fq}"
            t = info["table"]
            tid = self._table_node(t)
            b.add_edge(cid, tid, "MAPS_TO_TABLE", c.file, c.line, EXACT if info["explicit"] else RESOLVED,
                       via="$table" if info["explicit"] else "naming-convention")
            if t not in self.tables:
                self.stats["models_table_not_in_migrations"] += 1
            if info["connection"]:
                name = info["connection"]
                conn = self.connections.get(name) or b.add_node("connection", name, lang="php", attrs={"undefined": True})
                b.add_edge(cid, conn, "USES_CONNECTION", c.file, c.line, EXACT, via="$connection")
            for rn, rel in info["relations"].items():
                if rel["target"]:
                    tc = self.prog.cls(rel["target"])
                    if tc:
                        b.add_edge(cid, f"class:{tc.fqcn}", "HAS_RELATION", c.file, rel["line"], EXACT, relation=rel["kind"], method=rel["method"].name)
                b.add_node("method", rel["method"].id.split(":", 1)[1], attrs={"relation": rel["kind"], "related": rel["target"]})
        for fn_id, cids in self.registered.items():
            for cid in cids:
                b.add_edge(fn_id, cid, "REGISTERS_CONNECTION", None, None, RESOLVED)

    def _middleware_aliases(self):
        aliases = {}
        r = self.prog.scripts.get("bootstrap/app.php")
        facts = r.facts if r else []
        for f in facts:
            if f["t"] == "call" and (f.get("m") or "").lower() == "alias" and f.get("args") and f["args"][0].get("k") == "arr":
                for it in f["args"][0]["items"]:
                    k, v = it.get("key"), it.get("v")
                    if k and k.get("k") == "str" and classconst(v):
                        aliases[k["v"]] = classconst(v)
        return aliases

    def _routes_out(self):
        b, prog = self.b, self.prog
        aliases = self._middleware_aliases()
        from ..php.inline_guards import for_action
        n = 0
        for f, r in self.records.items():
            for rt in r.get("routes") or []:
                for verb in rt["methods"]:
                    key = f"{verb.upper()} {rt['full_uri']}"
                    # middleware given as a non-literal expression (a constant, a method call) comes back as a dict: kept
                    # out of the name list, counted
                    mws = [m for m in rt.get("middleware") or [] if isinstance(m, str)]
                    if len(mws) != len(rt.get("middleware") or []):
                        self.stats["route_middleware_unresolved"] = self.stats.get("route_middleware_unresolved", 0) + 1
                    attrs = {"name": rt.get("name") or None, "middleware": mws if rt.get("middleware") is not None else None,
                             "uri": rt["full_uri"], "method": verb.upper()}
                    if rt.get("webhook_client"):
                        attrs["webhook_client"] = rt["webhook_client"]
                        attrs["webhook_framework"] = "laravel-webhook-client"
                    if rt.get("scoped"):
                        attrs["scoped"] = True
                    fields = rt.get("binding_fields")
                    if isinstance(fields, dict) and fields:
                        attrs["binding_fields"] = fields
                    rid = b.add_node("route", key, name=key, file=f, line=rt["line"], module=module_of(f), lang="php",
                                     entry_kind="http_route", attrs=attrs)
                    n += 1
                    act = rt.get("action") or {}
                    if act.get("class"):
                        c = prog.cls(act["class"])
                        meth = prog.find_method(c.fqcn, act.get("method")) if c else None
                        if meth:
                            b.add_edge(rid, meth.id, "ROUTES_TO", f, rt["line"], EXACT)
                            # controller module for grouping
                            b.nodes[rid].module = module_of(c.file)
                        elif not self._cashier_route(c, rid, f, rt["line"]):
                            self.stats["routes_unresolved_action"] += 1
                        if self._is_cashier(c):
                            b.nodes[rid].attrs["webhook_framework"] = "laravel-cashier"
                            b.nodes[rid].attrs["controller"] = c.fqcn
                        igs = for_action(prog, act.get("class"), act.get("method"))
                        if igs:
                            b.nodes[rid].attrs["inline_guards"] = igs
                            self.stats["inline_guards"] += len(igs)
                    for mw in mws:
                        alias = mw.split(":")[0]
                        cls = aliases.get(alias) or (mw if prog.cls(mw) else None)
                        if cls and prog.cls(cls):
                            h = prog.find_method(cls, "handle")
                            if h:
                                b.add_edge(rid, h.id, "USES_MIDDLEWARE", f, rt["line"], RESOLVED, alias=alias)
        n += self._attribute_routes(aliases)
        self.stats["routes"] = n

    def _is_cashier(self, c) -> bool:
        if c is None:
            return False
        return any(a.endswith("Cashier\\Http\\Controllers\\WebhookController") for a in self.prog.ancestors(c.fqcn))

    def _cashier_route(self, c, rid, f, line) -> bool:
        """Route points at Cashier's inherited handleWebhook: the handle<Event> methods are the handlers."""
        if not self._is_cashier(c):
            return False
        from ...webhooks import cashier_event
        linked = False
        for m in c.methods.values():
            if cashier_event(m.name):
                self.b.add_edge(rid, m.id, "ROUTES_TO", f, line, HEURISTIC, how="cashier webhook")
                linked = True
        return linked

    def _attribute_routes(self, aliases) -> int:
        """spatie/laravel-route-attributes: #[Get]/#[Post]/... plus class Prefix and Middleware."""
        from ..php.inline_guards import for_action
        b, prog = self.b, self.prog
        n = 0
        linked = {(e.src, e.dst) for e in b.edges.values() if e.kind == "ROUTES_TO"}
        for c in prog.classes.values():
            if c.fqcn in prog.test_classes:
                continue
            class_prefix = _attr_prefixes(c.attributes)
            class_mw = _attr_middleware(c.attributes)
            for m in c.methods.values():
                method_prefix = _attr_prefixes(m.attribute_args)
                method_mw = _attr_middleware(m.attribute_args)
                for a in m.attribute_args or []:
                    spec = _route_attr(a)
                    if not spec:
                        continue
                    methods, uri, name, mw = spec
                    parts = [p for p in class_prefix + method_prefix + [uri.strip("/")] if p]
                    full = "/" + "/".join(parts)
                    mws = []
                    for item in class_mw + method_mw + mw:
                        if item not in mws:
                            mws.append(item)
                    line = a.get("line") or m.line
                    igs = for_action(prog, c.fqcn, m.name)
                    for verb in methods:
                        key = f"{verb} {full}"
                        nid = f"route:{key}"
                        if nid in b.nodes:
                            node = b.nodes[nid]
                            prev = [x for x in (node.attrs.get("middleware") or []) if isinstance(x, str)]
                            merged = list(prev)
                            for item in mws:
                                if item not in merged:
                                    merged.append(item)
                            if merged:
                                node.attrs["middleware"] = merged
                            if name and not node.attrs.get("name"):
                                node.attrs["name"] = name
                            node.attrs.setdefault("framework", "laravel-route-attributes")
                            if igs and not node.attrs.get("inline_guards"):
                                node.attrs["inline_guards"] = igs
                                self.stats["inline_guards"] += len(igs)
                            if (nid, m.id) not in linked:
                                b.add_edge(nid, m.id, "ROUTES_TO", c.file, line, EXACT)
                                linked.add((nid, m.id))
                            rid = nid
                        else:
                            attrs = {"name": name, "middleware": mws, "uri": full, "method": verb,
                                     "framework": "laravel-route-attributes"}
                            if igs:
                                attrs["inline_guards"] = igs
                                self.stats["inline_guards"] += len(igs)
                            rid = b.add_node("route", key, name=key, file=c.file, line=line, module=module_of(c.file),
                                             lang="php", entry_kind="http_route", attrs=attrs)
                            b.add_edge(rid, m.id, "ROUTES_TO", c.file, line, EXACT)
                            linked.add((rid, m.id))
                            n += 1
                        for item in mws:
                            alias = item.split(":")[0]
                            cls = aliases.get(alias) or (item if prog.cls(item) else None)
                            if cls and prog.cls(cls):
                                h = prog.find_method(cls, "handle")
                                if h:
                                    b.add_edge(rid, h.id, "USES_MIDDLEWARE", c.file, line, RESOLVED, alias=alias)
        self.stats["route_attribute_routes"] = n
        return n

    def _commands_out(self):
        b, prog = self.b, self.prog
        self.command_by_class = {}
        for c in prog.classes.values():
            if c.kind != "class" or prog.is_test_class(c.fqcn) or not prog.is_a(c.fqcn, "Illuminate\\Console\\Command"):
                continue
            sig = prog.find_prop(c.fqcn, "signature") or prog.find_prop(c.fqcn, "name")
            if not sig or not isinstance(sig.get("default"), str):
                continue
            name = sig["default"].strip().split()[0] if sig["default"].strip() else None
            if not name:
                continue
            h = prog.find_method(c.fqcn, "handle") or prog.find_method(c.fqcn, "__invoke")
            cid = b.add_node("command", name, name=name, file=c.file, line=c.line, module=module_of(c.file), lang="php",
                             entry_kind="artisan_command", doc=c.doc,
                             attrs={"class": c.fqcn, "signature": sig["default"].strip()})
            self.command_by_class[c.fqcn] = name
            if h:
                b.add_edge(cid, h.id, "HANDLED_BY", c.file, h.line, EXACT)
            self.stats["commands"] += 1

    def _schedules_out(self):
        b = self.b
        for fn, f in self.schedules:
            a0 = (f.get("args") or [None])[0]
            target = None
            if (f.get("m") or "").lower() == "command" and a0:
                if a0.get("k") == "str":
                    target = b.add_node("command", a0["v"].split(" ")[0])
                elif classconst(a0) and classconst(a0) in self.command_by_class:
                    target = f"command:{self.command_by_class[classconst(a0)]}"
            elif (f.get("m") or "").lower() == "job" and a0 and a0.get("k") == "new" and self.prog.cls(a0.get("class")):
                h = self.prog.find_method(a0["class"], "handle")
                target = h.id if h else None
            if not target:
                continue
            sid = b.add_node("schedule", f"{target.split(':', 1)[1]}@{fn.file}:{f['line']}", name=f"schedule {target.split(':', 1)[1]}",
                             file=fn.file, line=f["line"], module=module_of(fn.file), lang="php", entry_kind="scheduled")
            b.add_edge(sid, target, "SCHEDULES", fn.file, f["line"], EXACT)
            self.stats["schedules"] += 1

    def _jobs_listeners_admin_out(self):
        b, prog = self.b, self.prog
        for c in prog.classes.values():
            if c.kind != "class" or prog.is_test_class(c.fqcn):
                continue
            if self._is_job(c.fqcn):
                h = prog.find_method(c.fqcn, "handle")
                if h:
                    jid = b.add_node("job", c.fqcn, name=c.fqcn.split("\\")[-1], fqn=c.fqcn, file=c.file, line=c.line,
                                     module=module_of(c.file), lang="php", entry_kind="queue_job")
                    b.add_edge(jid, h.id, "HANDLED_BY", c.file, h.line, EXACT)
                    self.stats["jobs"] += 1
            if c.fqcn.startswith("App\\Listeners\\"):
                h = prog.find_method(c.fqcn, "handle")
                if h:
                    for p in h.params[:1]:
                        for et in prog.norm(p.get("types"), c.fqcn):
                            self.listeners.append((b.add_node("event", et, name=et.split("\\")[-1], fqn=et, lang="php"), h, None, h.line))
            if c.fqcn.startswith("App\\Filament\\"):
                aid = b.add_node("admin", c.fqcn, name=c.fqcn.split("\\")[-1], fqn=c.fqcn, file=c.file, line=c.line,
                                 module=module_of(c.file), lang="php", entry_kind="admin_panel")
                for m in c.methods.values():
                    b.add_edge(aid, m.id, "HANDLED_BY", c.file, m.line, RESOLVED, via="filament-surface")
                self.stats["admin_surfaces"] += 1
        for eid, target, fn, line in self.listeners:
            lid = b.add_node("listener", target.id.split(":", 1)[1], name=target.id.split("::")[-1], file=target.file, line=target.line,
                             module=module_of(target.file), lang="php", entry_kind="listener")
            b.add_edge(lid, target.id, "HANDLED_BY", target.file, target.line, EXACT)
            b.add_edge(eid, target.id, "LISTENED_BY", fn.file if fn else target.file, line, EXACT if fn else RESOLVED)
            self.stats["listeners"] += 1
        for model, obs, fn, line in self.observers:
            oc = prog.cls(obs)
            if not oc:
                continue
            b.add_edge(f"class:{model}", f"class:{oc.fqcn}", "OBSERVED_BY", fn.file, line, EXACT)
            oid = b.add_node("observer", oc.fqcn, name=oc.fqcn.split("\\")[-1], fqn=oc.fqcn, file=oc.file, line=oc.line,
                             module=module_of(oc.file), lang="php", entry_kind="observer")
            for m in oc.methods.values():
                b.add_edge(oid, m.id, "HANDLED_BY", oc.file, m.line, RESOLVED, via=f"observes {model.split(chr(92))[-1]}")

    def _bindings_out(self):
        b, prog = self.b, self.prog
        for fn, f, ctx in getattr(self, "bindings", []):
            args = f["args"]
            abstract = classconst(args[0])
            concrete = classconst(args[1]) if len(args) > 1 else None
            if not concrete and len(args) > 1 and args[1].get("k") == "closure":
                news = [g for g in fn.facts if g["t"] == "new" and (g.get("ctx") or {}).get("line") == f["line"] and g.get("class")]
                concrete = news[0]["class"] if news else None
            ac = prog.cls(abstract)
            if not ac:
                continue
            aid = f"{ac.kind}:{ac.fqcn}"
            b.add_edge(fn.id, aid, "BINDS", fn.file, f["line"], EXACT, method=f["m"])
            cc = prog.cls(concrete) if concrete else None
            if cc and cc.fqcn != ac.fqcn:
                b.add_edge(aid, f"{cc.kind}:{cc.fqcn}", "BOUND_TO", fn.file, f["line"], RESOLVED)
                for ln, m in ac.methods.items():
                    impl = prog.find_method(cc.fqcn, ln)
                    if impl and impl.id != m.id:
                        b.add_edge(m.id, impl.id, "BOUND_TO", fn.file, f["line"], RESOLVED)
            self.stats["bindings"] += 1
