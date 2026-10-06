"""Env/config keys and ORM data access for TypeScript server code (shared by the NestJS, Next.js and Express layers).

env / config
  process.env.X, process.env['X'], import.meta.env.X, `const { X } = process.env`  -> READS_ENV env:X (exact)
  ConfigService.get('ns.key') with a registerAs('ns', () => ({ key: process.env.X })) namespace
      -> READS_CONFIG config:ns.key (resolved), config:ns.key READS_ENV env:X (exact)
  ConfigService.get('X') (UPPER_SNAKE, no namespace) -> READS_ENV env:X (resolved)
  NEXT_PUBLIC_ / VITE_ / NUXT_PUBLIC_ keys are marked attrs.public (inlined into client bundles)

tables (node kind `table`, collections too: attrs.collection)
  TypeORM / MikroORM @Entity('t') | @Entity() (snake_case class name), sequelize-typescript @Table({tableName}),
  Mongoose @Schema({collection}) / mongoose.model('Cat', s) (lower-case plural)  -> MAPS_TO_TABLE, columns (CONTAINS)
  Prisma schema.prisma models (@@map / @map)                                       -> table + column nodes
  Drizzle pgTable/mysqlTable/sqliteTable('t', ...)                                  -> table for the variable
  reads/writes:
    this.<repo>.<op>() where <repo> is @InjectRepository(E) / Repository<E> / @InjectModel(E.name) / Model<E>
    prisma.<model>.<op>(), <ds>.getRepository(E).<op>() / manager.<op>(E, ...)
    Kysely selectFrom/insertInto/updateTable/deleteFrom('t'), knex('t'), Drizzle .from(t) / .insert(t) / ...
  -> READS_TABLE / WRITES_TABLE (resolved: needed type/name resolution; heuristic for name-shaped receivers)
"""
from __future__ import annotations

import re
from pathlib import Path

from ...core.plugin import GraphBuilder, Project
from .common import fw_facts, last_name, obj, plural, ref_nodes, snake, sval, svals
from ... import presets

# dependency / VCS directories every walk skips (cg_code_graph/presets/common.yaml skip_dirs)
COMMON_SKIP = presets.skip_dirs("common")

PUBLIC_ENV = re.compile(r"^(NEXT_PUBLIC_|VITE_|NUXT_PUBLIC_|REACT_APP_|EXPO_PUBLIC_|PUBLIC_)")
READ_OPS = {"findUnique", "findUniqueOrThrow", "findFirst", "findFirstOrThrow", "findMany", "count", "aggregate", "groupBy",
            "find", "findOne", "findOneBy", "findBy", "findAndCount", "findAndCountBy", "findOneOrFail", "findOneByOrFail", "findByIds",
            "exist", "exists", "existsBy", "createQueryBuilder", "findAll", "findByPk", "findById", "countDocuments",
            "estimatedDocumentCount", "distinct", "selectFrom", "from", "innerJoin", "leftJoin", "rightJoin", "fullJoin", "query"}
KNEX_WRITES = {"insert", "update", "del", "delete", "upsert", "merge", "truncate", "increment", "decrement"}
WRITE_OPS = {"create", "createMany", "createManyAndReturn", "update", "updateMany", "updateManyAndReturn", "upsert", "delete",
             "deleteMany", "save", "insert", "remove", "softDelete", "softRemove", "restore", "increment", "decrement",
             "findOrCreate", "bulkCreate", "destroy", "findByIdAndUpdate", "findByIdAndDelete", "findOneAndUpdate",
             "findOneAndDelete", "findOneAndReplace", "updateOne", "deleteOne", "insertMany", "replaceOne", "bulkWrite",
             "insertInto", "updateTable", "deleteFrom", "replaceInto", "mergeInto", "into"}
ENTITY_DECOS = {"Entity", "ViewEntity", "Table", "Schema"}
COLUMN_DECOS = {"Column", "PrimaryColumn", "PrimaryGeneratedColumn", "CreateDateColumn", "UpdateDateColumn", "DeleteDateColumn",
                "VersionColumn", "ObjectIdColumn", "Property", "PrimaryKey", "Prop", "ForeignKey", "Index"}
RELATION_DECOS = {"ManyToOne", "OneToMany", "OneToOne", "ManyToMany", "BelongsTo", "HasMany", "HasOne", "BelongsToMany"}
DRIZZLE_TABLES = {"pgTable", "mysqlTable", "sqliteTable", "singlestoreTable", "table"}


def _ensure_table(b: GraphBuilder, name: str, **attrs) -> str:
    return b.add_node("table", name, name=name, lang="ts", attrs=attrs)


def _ensure_column(b: GraphBuilder, table: str, col: str, file=None, line=None) -> str:
    tid = _ensure_table(b, table)
    cid = b.add_node("column", f"{table}.{col}", name=f"{table}.{col}", file=file, line=line, lang="ts", attrs={"table": table})
    b.add_edge(tid, cid, "CONTAINS", file=file, line=line, confidence="exact")
    return cid


def prisma_files(root: Path) -> list:
    files = []
    for pat in ("schema.prisma", "prisma/schema.prisma", "prisma/*.prisma", "prisma/schema/*.prisma", "src/prisma/*.prisma"):
        files += sorted(root.glob(pat))
    if not files:
        files = [p for p in sorted(root.rglob("*.prisma")) if COMMON_SKIP.isdisjoint(p.parts)][:20]
    return list(dict.fromkeys(files))


PRISMA_PROVIDERS = {"postgresql": "postgres", "postgres": "postgres", "cockroachdb": "postgres", "mysql": "mysql",
                    "sqlserver": "mssql", "mongodb": "mongodb"}


def prisma_config_url(root: Path, schema_dir: Path):
    for d in dict.fromkeys((schema_dir, schema_dir.parent, root)):
        for name in ("prisma.config.ts", "prisma.config.mts", "prisma.config.js", "prisma.config.mjs"):
            try:
                txt = (d / name).read_text()
            except Exception:
                continue
            m = re.search(r"\bdatasource\s*:\s*\{[^}]*?\burl\s*:\s*(?:env\(\s*['\"](\w+)['\"]\s*\)|process\.env\.(\w+)"
                          r"|process\.env\[\s*['\"](\w+)['\"]\s*\]|['\"]([^'\"]+)['\"])", txt, re.S)
            if m:
                k = m.group(1) or m.group(2) or m.group(3)
                return ("env", k, None) if k else ("lit", m.group(4))
    return None


def prisma_datasources(root: Path, models: dict) -> list:
    """`datasource db { provider = "postgresql"  url = env("DATABASE_URL") }` -> external facts (#41): the
    system the schema's models live in. sqlite is a local file, not a system."""
    out = []
    for f in prisma_files(root):
        try:
            txt = f.read_text()
        except Exception:
            continue
        rel = str(f.relative_to(root))
        for m in re.finditer(r"^datasource\s+(\w+)\s*\{(.*?)^\}", txt, re.S | re.M):
            body = re.sub(r"(?m)(^|\s)//[^\n]*", r"\1", m.group(2))   # not the // of a URL
            pv = re.search(r"^\s*provider\s*=\s*\"([^\"]+)\"", body, re.M)
            proto = PRISMA_PROVIDERS.get((pv.group(1) if pv else "").lower())
            if proto is None:
                continue
            line = txt[:m.start()].count("\n") + 1
            uv = re.search(r"^\s*url\s*=\s*(?:env\(\s*\"(\w+)\"\s*\)|\"([^\"]+)\")", body, re.M)
            url = ("env", uv.group(1), None) if uv and uv.group(1) else ("lit", uv.group(2)) if uv else None
            if url is None:          # Prisma 7: the url moved to prisma.config.ts (`datasource: { url: env('X') }`)
                url = prisma_config_url(root, f.parent)
            # models of the same schema (prisma/schema/*.prisma folders share one datasource)
            tables = sorted({mm["table"] for mm in models.values() if Path(mm["file"]).parent == Path(rel).parent})
            if url is None:
                continue
            out.append({"datasource": m.group(1), "protocol": proto, "url": url, "file": rel, "line": line,
                        "var": m.group(1), "module": rel, "library": "prisma", "tables": tables})
    return out



SQL_DIALECTS = {"postgres": "postgres", "postgresql": "postgres", "pg": "postgres", "cockroachdb": "postgres",
                "mysql": "mysql", "mariadb": "mysql", "mysql2": "mysql", "mssql": "mssql", "sqlserver": "mssql",
                "oracle": "oracle", "mongodb": "mongodb", "mongo": "mongodb"}


def _env_or_lit(expr: str):
    """`process.env.X` / `env('X')` / `"lit"` / `env.X` -> ('env', KEY, None) | ('lit', value) | None."""
    e = (expr or "").strip()
    m = re.match(r"""(?:process\.env\.(\w+)|process\.env\[\s*['\"](\w+)['\"]\s*\]|env\(\s*['\"](\w+)['\"]\s*\)|(?:import\.meta\.env|environment|env)\.(\w+)|['\"]([^'\"]+)['\"])""", e)
    if not m:
        return None
    k = m.group(1) or m.group(2) or m.group(3) or m.group(4)
    return ("env", k, None) if k else ("lit", m.group(5))


def typeorm_datasources(root: Path, entity_tables: list) -> list:
    """`new DataSource({ type: 'postgres'|process.env.DATABASE_TYPE, url|host: ... })` (#41)."""
    out, files = [], []
    for pat in ("**/data-source.ts", "**/data-source.js", "**/ormconfig.ts", "**/ormconfig.js",
                "**/database.module.ts", "**/typeorm*.ts", "**/app.module.ts"):
        files += [p for p in root.glob(pat) if COMMON_SKIP.isdisjoint(p.parts)]
    files = list(dict.fromkeys(files))[:30]
    for f in files:
        try:
            txt = f.read_text()
        except Exception:
            continue
        if "DataSource" not in txt and "typeorm" not in txt.lower():
            continue
        rel = str(f.relative_to(root))
        for m in re.finditer(r"""\bnew\s+DataSource\s*\(\s*\{""", txt):
            # balanced object literal (Nest configs nest `extra: { ssl: {..} }`)
            i, depth = m.end() - 1, 0
            while i < len(txt):
                c = txt[i]
                if c in ("\"", "\'", "`"):
                    q = c; i += 1
                    while i < len(txt) and txt[i] != q:
                        i += 2 if txt[i] == "\\" else 1
                elif c == "{":
                    depth += 1
                elif c == "}":
                    depth -= 1
                    if depth == 0:
                        break
                i += 1
            else:
                continue
            body = re.sub(r"(?m)(^|\s)//[^\n]*", r"\1", txt[m.end():i])
            tv = re.search(r"""\btype\s*:\s*([^,\n]+)""", body)
            if not tv:
                continue
            texpr = tv.group(1).strip().rstrip(",")
            proto = None
            tm = re.match(r"""['\"](\w+)['\"]""", texpr)
            if tm:
                proto = SQL_DIALECTS.get(tm.group(1).lower())
            else:
                ev = _env_or_lit(texpr)
                # type from env: default to postgres when DATABASE_URL / DATABASE_TYPE (Nest boilerplate)
                if ev and ev[0] == "env":
                    proto = "postgres"   # refined below from url scheme if present
            uv = re.search(r"""\b(?:url|connectionString)\s*:\s*([^,\n]+)""", body)
            hv = re.search(r"""\bhost\s*:\s*([^,\n]+)""", body)
            url = _env_or_lit(uv.group(1).strip().rstrip(",")) if uv else None
            host = _env_or_lit(hv.group(1).strip().rstrip(",")) if hv else None
            if url is None and host is None:
                continue
            if proto is None and url and url[0] == "lit":
                from ...external import parse_dsn
                d = parse_dsn(url[1])
                proto = d["protocol"] if d else None
            if proto is None:
                proto = "postgres" if (url and url[0] == "env" and "MYSQL" not in (url[1] or "").upper()) else None
            if proto is None:
                continue
            line = txt[:m.start()].count("\n") + 1
            fact = {"datasource": "default", "protocol": proto, "file": rel, "line": line,
                    "var": "default", "module": rel, "library": "typeorm", "tables": sorted(entity_tables)}
            if url:
                fact["url"] = url
            else:
                fact["host"] = host
                pv = re.search(r"""\bport\s*:\s*([^,\n]+)""", body)
                if pv:
                    fact["port"] = _env_or_lit(pv.group(1).strip().rstrip(","))
            out.append(fact)
    return out


def drizzle_datasources(root: Path, drizzle_tables: list) -> list:
    """`drizzle.config.ts` dialect + dbCredentials.url (#41)."""
    out = []
    files = []
    for pat in ("drizzle.config.ts", "drizzle.config.js", "drizzle.config.mts", "drizzle.config.mjs",
                "**/drizzle.config.ts", "**/drizzle.config.js"):
        files += [p for p in root.glob(pat) if COMMON_SKIP.isdisjoint(p.parts)]
    for f in list(dict.fromkeys(files))[:10]:
        try:
            txt = f.read_text()
        except Exception:
            continue
        rel = str(f.relative_to(root))
        body = re.sub(r"(?m)(^|\s)//[^\n]*", r"\1", txt)
        dv = re.search(r"""\bdialect\s*:\s*['\"](\w+)['\"]""", body)
        proto = SQL_DIALECTS.get((dv.group(1) if dv else "").lower())
        if proto is None:
            # driver: 'pg' form
            dv = re.search(r"""\bdriver\s*:\s*['\"](\w+)['\"]""", body)
            proto = SQL_DIALECTS.get((dv.group(1) if dv else "").lower())
        if proto is None:
            continue
        # dbCredentials: { url: process.env.X } or connectionString
        uv = re.search(r"""\b(?:url|connectionString)\s*:\s*([^,\n}]+)""", body)
        url = None
        if uv:
            url = _env_or_lit(uv.group(1).strip())
            if url is None:
                # `url: nonPoolingUrl` where `const nonPoolingUrl = process.env.POSTGRES_URL.replace(...)`
                name = uv.group(1).strip()
                if re.fullmatch(r"[A-Za-z_]\w*", name):
                    cm = re.search(rf"""\b(?:const|let|var)\s+{re.escape(name)}\s*=\s*([^;\n]+)""", body)
                    if cm:
                        # process.env.X or process.env.X.replace(...)
                        em = re.search(r"""process\.env\.(\w+)|process\.env\[\s*['\"](\w+)['\"]""", cm.group(1))
                        if em:
                            url = ("env", em.group(1) or em.group(2), None)
        if url is None:
            continue
        line = 1
        dm = re.search(r"""\bdialect\s*:""", body) or re.search(r"""\bdbCredentials\s*:""", body)
        if dm:
            line = txt[:dm.start()].count("\n") + 1
        out.append({"datasource": "default", "protocol": proto, "url": url, "file": rel, "line": line,
                    "var": "default", "module": rel, "library": "drizzle", "tables": sorted(drizzle_tables)})
    return out


def parse_drizzle_tables(root: Path) -> dict:
    """`export const users = pgTable('users', ...)` / mysqlTable / sqliteTable -> {var: table} (#41)."""
    out = {}
    files = [p for p in root.rglob("*.ts") if COMMON_SKIP.isdisjoint(p.parts)
             and ("schema" in p.name.lower() or "drizzle" in str(p.parent).lower())][:40]
    rx = re.compile(r"""(?:export\s+)?(?:const|let)\s+(\w+)\s*=\s*(?:\w+\.)?(pgTable|mysqlTable|sqliteTable|singlestoreTable)\(\s*['"]([^'"]+)['"]""")
    for f in files:
        try:
            txt = f.read_text()
        except Exception:
            continue
        if "Table(" not in txt:
            continue
        rel = str(f.relative_to(root))
        for m in rx.finditer(txt):
            out[m.group(1)] = {"table": m.group(3), "file": rel, "line": txt[:m.start()].count("\n") + 1, "driver": m.group(2)}
    return out


def parse_prisma(root: Path) -> dict:
    """model Name { field Type @map("col") ... @@map("table") } -> {Name: {table, fields: {field: column}, file, line}}"""
    models = {}
    files = []
    for pat in ("schema.prisma", "prisma/schema.prisma", "prisma/*.prisma", "prisma/schema/*.prisma", "src/prisma/*.prisma"):
        files += sorted(root.glob(pat))
    if not files:
        files = [p for p in sorted(root.rglob("*.prisma")) if COMMON_SKIP.isdisjoint(p.parts)][:20]
    for f in dict.fromkeys(files):
        try:
            txt = f.read_text()
        except Exception:
            continue
        rel = str(f.relative_to(root))
        for m in re.finditer(r"^model\s+(\w+)\s*\{(.*?)^\}", txt, re.S | re.M):
            name, body = m.group(1), m.group(2)
            line = txt[:m.start()].count("\n") + 1
            mp = re.search(r"@@map\(\s*(?:name:\s*)?\"([^\"]+)\"", body)
            fields = {}
            for fm in re.finditer(r"^\s*(\w+)\s+(\w+)(\[\])?\??([^\n]*)$", body, re.M):
                fname, ftype, rest = fm.group(1), fm.group(2), fm.group(4)
                if fname.startswith("@@"):
                    continue
                if "@relation" in rest or (ftype[0].isupper() and ftype not in ("String", "Int", "BigInt", "Float", "Decimal", "Boolean", "DateTime", "Json", "Bytes")):
                    continue
                cm = re.search(r"@map\(\s*(?:name:\s*)?\"([^\"]+)\"", rest)
                fields[fname] = cm.group(1) if cm else fname
            models[name] = {"table": mp.group(1) if mp else name, "fields": fields, "file": rel, "line": line}
    return models


def contribute_data(project: Project, b: GraphBuilder, ctx) -> dict:
    F = fw_facts(ctx)
    st = {"env_reads": 0, "config_reads": 0, "tables": 0, "table_reads": 0, "table_writes": 0}
    if not F:
        return st
    classes = {c["id"]: c for c in F.get("classes") or []}
    instances = F.get("instances") or {}

    # ---- config namespaces (registerAs)
    cfg_defs = {}
    for d in F.get("config_defs") or []:
        cid = b.add_node("config", d["key"], name=d["key"], file=d["file"], line=d["line"], lang="ts", attrs={"namespace": d["ns"]})
        cfg_defs[d["key"]] = cid
        for k in d["env"]:
            b.add_edge(cid, _env(b, k), "READS_ENV", file=d["file"], line=d["line"], confidence="exact")
    namespaces = {d["ns"] for d in F.get("config_defs") or []}
    for e in F.get("env") or []:
        if not e.get("src") or not b.has(e["src"]):
            continue
        key = e["key"]
        if e["via"].startswith("ConfigService"):
            if key in cfg_defs or key.split(".")[0] in namespaces:
                cid = cfg_defs.get(key) or b.add_node("config", key, name=key, lang="ts", attrs={"namespace": key.split(".")[0]})
                b.add_edge(e["src"], cid, "READS_CONFIG", file=e["file"], line=e["line"], confidence="resolved", via=e["via"])
                st["config_reads"] += 1
            elif re.fullmatch(r"[A-Z][A-Z0-9_]*", key):
                b.add_edge(e["src"], _env(b, key), "READS_ENV", file=e["file"], line=e["line"], confidence="resolved", via=e["via"])
                st["env_reads"] += 1
            else:
                cid = b.add_node("config", key, name=key, lang="ts")
                b.add_edge(e["src"], cid, "READS_CONFIG", file=e["file"], line=e["line"], confidence="resolved", via=e["via"])
                st["config_reads"] += 1
            continue
        # `env.X` wrappers and values parsed from process.env (#103) name the key by convention: resolved
        conf = "resolved" if e["via"].startswith(("env wrapper", "env schema")) else "exact"
        b.add_edge(e["src"], _env(b, key), "READS_ENV", file=e["file"], line=e["line"], confidence=conf, via=e["via"])
        st["env_reads"] += 1

    # ---- entities -> tables
    entity_table = {}      # class node id -> table
    model_name_table = {}  # Mongoose model name -> collection
    for c in classes.values():
        decos = {last_name(d["name"]): d for d in c.get("decorators") or []}
        hit = next((n for n in ENTITY_DECOS if n in decos), None)
        if not hit or not c.get("name"):
            continue
        d = decos[hit]
        a0 = (d.get("args") or [None])[0]
        if hit == "Schema" and not (d.get("mod") or "").startswith("@nestjs/mongoose") and d.get("mod"):
            continue
        explicit = sval(a0) or sval(obj(a0).get("name")) or sval(obj(a0).get("tableName")) or sval(obj(a0).get("collection"))
        # sequelize-typescript @Table; Immich @immich/sql-tools @Table('t'); skip bare @Table from unknown mods
        if hit == "Table" and d.get("mod") and "sequelize" not in d["mod"] and not explicit:
            continue
        if hit == "Schema":
            table, kind = explicit or plural(c["name"].lower()), {"collection": True}
        elif hit == "Table":
            table, kind = explicit or plural(c["name"]), {}
        else:
            table, kind = explicit or snake(c["name"]), {}
        tid = _ensure_table(b, table, orm=hit, **kind)
        entity_table[c["id"]] = table
        model_name_table[c["name"]] = table
        if b.has(c["id"]):
            b.add_edge(c["id"], tid, "MAPS_TO_TABLE", file=c["file"], line=c["line"], confidence="exact" if explicit else "resolved",
                       via=f"@{hit}" if explicit else "naming-convention")
        for p in c.get("props") or []:
            pd = {last_name(x["name"]): x for x in p.get("decorators") or []}
            col = next((n for n in COLUMN_DECOS if n in pd), None)
            rel = next((n for n in RELATION_DECOS if n in pd), None)
            if col:
                args = pd[col].get("args") or []
                cname = next((sval(obj(a).get("name")) for a in args if sval(obj(a).get("name"))), None) or p["name"]
                _ensure_column(b, table, cname, c["file"], p["line"])
            if rel:
                for a in pd[rel].get("args") or []:
                    for n in ref_nodes(a.get("body") or a if isinstance(a, dict) else None) + [((a or {}).get("body") or {}).get("node")]:
                        if n and n.startswith("class:") and b.has(c["id"]):
                            b.add_edge(c["id"], n, "HAS_RELATION", file=c["file"], line=p["line"], confidence="exact", relation=rel, property=p["name"])
                            break
                    else:
                        continue
                    break
    st["entities"] = len(entity_table)

    # mongoose.model('Cat', CatSchema)
    for mc in F.get("member_calls") or []:
        if mc["method"] == "model" and sval(mc.get("a0")):
            pass
    prisma = parse_prisma(project.root)
    prisma_by_client = {}
    for name, m in prisma.items():
        tid = _ensure_table(b, m["table"], orm="prisma", model=name)
        b.nodes[tid].file = b.nodes[tid].file or m["file"]
        b.nodes[tid].line = b.nodes[tid].line or m["line"]
        for f, col in m["fields"].items():
            _ensure_column(b, m["table"], col, m["file"], m["line"])
        prisma_by_client[name[0].lower() + name[1:]] = m["table"]
    st["prisma_models"] = len(prisma)
    if (ds := prisma_datasources(project.root, prisma)):
        b.external_facts = getattr(b, "external_facts", []) + ds
    drizzle = {}   # instance key -> table
    drizzle_names = {}
    for k, inst in instances.items():
        init = (inst or {}).get("init") or {}
        if "call" in init and last_name(init["call"]) in DRIZZLE_TABLES and sval((init.get("args") or [None])[0]) and (init.get("mod") or "").startswith("drizzle"):
            t = sval(init["args"][0])
            drizzle[k] = t
            drizzle_names[inst.get("name")] = t
            _ensure_table(b, t, orm="drizzle")
    st["drizzle_tables"] = len(drizzle)
    for name, info in parse_drizzle_tables(project.root).items():
        t = info["table"]
        drizzle_names[name] = t
        if name not in drizzle:
            drizzle[name] = t
        _ensure_table(b, t, orm="drizzle")
    st["drizzle_tables"] = len(set(drizzle.values()) | set(drizzle_names.values()))

    # repositories / models injected into classes: (class id, prop name) -> table
    repo_prop = {}
    for c in classes.values():
        members = [(p, p.get("decorators") or []) for p in c.get("ctor") or []] + [(p, p.get("decorators") or []) for p in c.get("props") or []]
        for p, decos in members:
            table = None
            for d in decos:
                n = last_name(d["name"])
                a0 = (d.get("args") or [None])[0] or {}
                if n == "InjectRepository":
                    table = entity_table.get(a0.get("node"))
                elif n == "InjectModel":
                    ref = (a0.get("ref") or sval(a0) or "").replace(".name", "")
                    table = model_name_table.get(ref) or entity_table.get(a0.get("node"))
            t = p.get("type") or {}
            if not table and t.get("name") in ("Repository", "Model", "EntityRepository", "MongoRepository", "TreeRepository") and t.get("args"):
                table = entity_table.get((t["args"][0] or {}).get("id"))
            if table:
                repo_prop[(c["id"], p["name"])] = table
    supers = {c["id"]: (c.get("extends") or {}).get("id") for c in classes.values()}

    def prop_table(cls, prop):
        for _ in range(6):
            if not cls:
                return None
            if (cls, prop) in repo_prop:
                return repo_prop[(cls, prop)]
            cls = supers.get(cls)
        return None

    for mc in F.get("member_calls") or []:
        src, m = mc.get("src"), mc["method"]
        if not src or not b.has(src):
            continue
        tables, conf, via = [], "resolved", None
        a0 = mc.get("a0") or {}
        if mc.get("prop") and (m in READ_OPS or m in WRITE_OPS):
            t = prop_table(mc.get("cls"), mc["prop"])
            if t:
                tables, via = [t], "injected-repository"
            elif m in ("selectFrom", "insertInto", "updateTable", "deleteFrom", "replaceInto", "mergeInto") and svals(a0):
                tables, via = [s.split(" ")[0] for s in svals(a0)], "kysely"
        elif mc.get("chain") and (m in READ_OPS or m in WRITE_OPS):
            ch = mc["chain"]
            if len(ch) >= 2 and ch[-1] in prisma_by_client and (re.search(r"prisma|db|tx|client|trx", ".".join(ch[:-1]), re.I) or "Prisma" in (mc.get("recv_type") or "")):
                tables, via = [prisma_by_client[ch[-1]]], "prisma"
            elif len(ch) >= 3 and ch[-2] == "query" and ch[-1] in drizzle_names:
                tables, via = [drizzle_names[ch[-1]]], "drizzle-query"
            elif m in ("selectFrom", "insertInto", "updateTable", "deleteFrom", "replaceInto", "mergeInto") and svals(a0):
                tables, via = [s.split(" ")[0] for s in svals(a0)], "kysely"
            elif a0.get("node") in entity_table:
                tables, via = [entity_table[a0["node"]]], "entity-argument"
        elif m == "knex" and svals(a0):
            tables, via, conf = [s.split(" ")[0] for s in svals(a0)], "knex", "heuristic"
        elif mc.get("chained") or mc.get("root"):
            if a0.get("key") in drizzle:
                tables, via = [drizzle[a0["key"]]], "drizzle"
            elif m in ("selectFrom", "insertInto", "updateTable", "deleteFrom", "replaceInto", "mergeInto", "innerJoin", "leftJoin") and svals(a0):
                tables, via = [s.split(" ")[0] for s in svals(a0)], "kysely"
            elif m in ("from", "into", "table") and svals(a0) and re.search(r"knex|db|trx|qb|query|sql", mc.get("root") or "", re.I):
                tables, via, conf = [s.split(" ")[0] for s in svals(a0)], "query-builder", "heuristic"
        if not tables and a0.get("key") in drizzle and m in ("from", "insert", "update", "delete", "into", "innerJoin", "leftJoin"):
            tables, via = [drizzle[a0["key"]]], "drizzle"
        for t in tables:
            if not t or "{" in t:
                continue
            tid = _ensure_table(b, t, **({"orm": "kysely"} if via == "kysely" else {}))
            write = m in WRITE_OPS or (via == "drizzle" and m in ("insert", "update", "delete")) \
                or (via == "knex" and bool(KNEX_WRITES & set(mc.get("chain") or ())))
            b.add_edge(src, tid, "WRITES_TABLE" if write else "READS_TABLE", file=mc["file"], line=mc["line"], confidence=conf, via=via, op=m)
            st["table_writes" if write else "table_reads"] += 1
    # TypeORM DataSource / Drizzle config -> external systems (#41 step 2)
    typeorm_tables = sorted({n.name for n in b.nodes.values()
                             if n.kind == "table" and (n.attrs or {}).get("orm") in ("Entity", "ViewEntity", "Table")})
    drizzle_names_list = sorted(set(drizzle.values()) | set(drizzle_names.values()))
    extra = typeorm_datasources(project.root, typeorm_tables) + drizzle_datasources(project.root, drizzle_names_list)
    if extra:
        b.external_facts = getattr(b, "external_facts", []) + extra
    st["tables"] = sum(1 for n in b.nodes.values() if n.kind == "table" and n.lang == "ts")
    return st


def _env(b: GraphBuilder, key: str) -> str:
    return b.add_node("env", key, name=key, lang="ts", attrs={"public": True} if PUBLIC_ENV.match(key) else {})
