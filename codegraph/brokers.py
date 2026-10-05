"""Message brokers and pub/sub as protocol endpoints (#35 part 1): producers and consumers paired by topic, routing
key, channel or subject, within a repo and across repos (`cg link`).

  endpoint:kafka:<topic>                 producer.send({topic}) / produce(topic) -> consumer subscriptions (attrs.group)
  endpoint:amqp:<exchange>/<key>         publish(exchange, routing key) -> the queues bound to the exchange (topic
                                         exchanges: `*` / `#` keys; fanout and headers exchanges: `<exchange>/#`)
  endpoint:amqp:queue:<name>             the default exchange (sendToQueue(q) / publish("", q)) -> consume(q)
  endpoint:redis-pubsub:<channel>        PUBLISH -> SUBSCRIBE / PSUBSCRIBE (globs)
  endpoint:redis-stream:<key>            XADD -> XREAD / XREADGROUP (attrs.group)
  endpoint:mqtt:<topic>                  publish -> subscribe (`+` / `#`)
  endpoint:nats:<subject>                publish / request -> subscribe (`*` / `>`, attrs.group: queue group)

Libraries:
  JS / TS   kafkajs, @confluentinc/kafka-javascript, node-rdkafka; amqplib, amqp-connection-manager; ioredis,
            node-redis; mqtt (MQTT.js); nats
  Python    confluent-kafka, kafka-python, aiokafka, faust; pika, aio-pika; redis-py (redis.asyncio); paho-mqtt,
            aiomqtt / asyncio-mqtt, gmqtt; nats-py
  PHP       php-amqplib; Laravel `Redis::publish` / `Redis::subscribe`, Predis / phpredis; php-mqtt/client

Names: string literals, constants (same file, imported upper-case constants, TS enums / `as const` members), locals
and `this.x` / `self.x` fields assigned one, templates / f-strings / concatenation (`orders.{id}`, heuristic),
`process.env.X ?? "d"` / `os.getenv("X", "d")` (the default, heuristic) and `env:X` for an env key without a default
(matches only another use of the same key). A receiver whose whole name is unknown is not recorded (counted).
Handlers: the named callback (`consume(q, onMessage)`, `on('message', h)`, `cb=h`, `eachMessage: h`), else the
function around the subscription (inline callbacks, `for await` loops); script code: the module.
"""
from __future__ import annotations

import re
from collections import defaultdict

from .core.model import HEURISTIC, RESOLVED

JS_EXT = (".ts", ".tsx", ".js", ".mjs", ".cjs", ".mts", ".cts")
TEST_FILE = re.compile(r"(?:^|/)(?:tests?|__tests__|spec)/|(?:^|/)test_[^/]*\.py$|_tests?\.py$|(?:^|/)conftest\.py$|"
                       r"\.(?:spec|test)\.[cm]?[jt]sx?$|Test\.php$")
# libraries per language: protocol -> import pattern
JS_LIBS = {
    "kafka": r"kafkajs|@confluentinc/kafka-javascript|node-rdkafka",
    "amqp": r"amqplib(?:/callback_api)?|amqp-connection-manager",
    "redis": r"ioredis|redis|@redis/client",
    "mqtt": r"mqtt|async-mqtt|precompiled-mqtt",
    "nats": r"nats|nats\.ws|@nats-io/[\w-]+",
}
PY_LIBS = {
    "kafka": r"confluent_kafka|kafka|aiokafka|faust",
    "amqp": r"pika|aio_pika|aiormq",
    "redis": r"redis(?:\.asyncio)?|aioredis",
    "mqtt": r"paho(?:\.mqtt)?|aiomqtt|asyncio_mqtt|gmqtt",
    "nats": r"nats",
}
PHP_LIBS = {
    "amqp": r"PhpAmqpLib\\",
    "redis": r"Illuminate\\Support\\Facades\\Redis|Predis\\|new\s+\\?Redis\s*\(",
    "mqtt": r"PhpMqtt\\Client",
}
KT_LIBS = {
    "kafka": r"org\.apache\.kafka|org\.springframework\.kafka",
    "amqp": r"com\.rabbitmq\.client|org\.springframework\.amqp|dev\.kourier\.amqp",
    "mqtt": r"org\.eclipse\.paho|com\.hivemq\.client",
    "nats": r"io\.nats",
    "redis": r"redis\.clients\.jedis|io\.lettuce|org\.springframework\.data\.redis",
}
RS_LIBS = {
    "kafka": r"rdkafka", "amqp": r"lapin|amqprs", "nats": r"async_nats|nats", "mqtt": r"rumqttc|paho_mqtt",
    "redis": r"redis",
}
# object names that tell the protocol of a `.publish(` / `.subscribe(` without an import in the file
HINTS = {"kafka": r"kafka|producer|consumer", "amqp": r"amqp|rabbit|channel|\bch\b|chan\b",
         "redis": r"redis|publisher|subscriber", "mqtt": r"mqtt", "nats": r"nats|\bnc\b|jetstream|\bjs\b"}
PUBSUB = ("redis", "mqtt", "nats")
WRAPPED = {"publish", "publishAsync", "subscribe", "subscribeAsync", "psubscribe", "pSubscribe", "request", "xadd", "xAdd",
           "xreadgroup", "xReadGroup", "xread", "xRead", "sendToQueue", "consume", "bindQueue"}
JSSTR = r"""(?:'([^'\\\n]*)'|"([^"\\\n]*)"|`([^`$\\]*)`)"""


def _args(src, paren, limit=3000):
    from .process_runs import _args_text
    from .sockets import split_args
    return split_args(_args_text(src, paren, limit))


def _kw(args, name):
    """Value of keyword / object key `name` among call arguments (Python `k=v`, JS `{k: v}` handled by _key)."""
    for a in args:
        k, eq, v = a.partition("=")
        if eq and k.strip() == name and not v.startswith("="):
            return v.strip()
    return None


def _key(obj, name):
    """Value text of key `name` of a JS / Python object or dict literal `{...}` (top level), or None."""
    from .sockets import split_args
    obj = (obj or "").strip()
    if not (obj.startswith("{") and obj.endswith("}")):
        return None
    for a in split_args(obj[1:-1]):
        m = re.match(r"""\s*(?:['"]?)([\w.$-]+)(?:['"]?)\s*:\s*([\s\S]*)$""", a)
        if m and m.group(1) == name:
            return m.group(2).strip()
        if a.strip() == name:                 # shorthand `{ topic }`
            return name
    return None


def _strlit(e):
    e = (e or "").strip()
    m = re.fullmatch(r"""[rbuRBU]?(?:'([^'\\\n]*)'|"([^"\\\n]*)")|`([^`$\\]*)`""", e)
    if m:
        return next(g for g in m.groups() if g is not None)
    return None


def _list(e):
    """Elements of a list / tuple / array literal, else [e]."""
    from .sockets import split_args
    e = (e or "").strip()
    if (e.startswith("[") and e.endswith("]")) or (e.startswith("(") and e.endswith(")") and "," in e):
        return split_args(e[1:-1])
    return [e]


class Scan:
    def __init__(self, project, b, sock=None):
        from .sockets import Scan as SockScan
        self.root, self.b = project.root, b
        self.s = sock or SockScan(project, b)
        self.st = defaultdict(int)
        self.samples = defaultdict(list)
        self.done = set()
        self.used = set()                       # protocols whose library the project imports
        self._enums = None
        self._mods = None
        self._field_defs = None  # class field defaults (pydantic / dataclass snake_case) (#157)
        self.exch_type = {}                     # exchange name -> direct / topic / fanout / headers
        self.bindings = defaultdict(set)        # queue identity -> {(exchange, key, file, line)}
        self.consumes = []                      # (queue identity, handler, file, line, conf, how, lib)
        from .tests_index import is_test_node
        # (class, method) of the repo's own messaging wrappers (`class Mqtt { publish() }` called as `this.mqtt.publish`)
        self.own_methods = set()
        for n in b.nodes.values():
            if n.kind == "method" and not is_test_node(n):
                parts = re.split(r"[.#:]+", n.name or n.id)
                if len(parts) >= 2 and parts[-1] in WRAPPED:
                    self.own_methods.add((parts[-2].lower(), parts[-1]))

    def miss(self, key, text):
        self.st[key] += 1
        if len(self.samples[key]) < 6 and text[:100] not in self.samples[key]:
            self.samples[key].append(text[:100])

    # ---------------------------------------------------------------- helpers
    def module_of(self, file):
        if self._mods is None:
            self._mods = {}
            for nid, n in self.b.nodes.items():
                if n.kind in ("module", "script", "file") and n.file:
                    self._mods.setdefault(n.file, nid)
        return self._mods.get(file)

    def fn_at(self, file, pos):
        fn, _lo, _hi = self.s.fn_bounds(file, pos)
        return fn or self.module_of(file)

    def is_test(self, file, fn):
        from .tests_index import is_test_node
        n = self.b.nodes.get(fn)
        return bool(TEST_FILE.search(file)) or bool(n and is_test_node(n))

    def handler(self, file, pos, expr):
        """The named callback `expr`, else the function around `pos` (inline callbacks)."""
        h = None
        e = (expr or "").strip()
        if e and re.fullmatch(r"(?:self\.|this\.|\$this->|\$)?[A-Za-z_][\w.]*(?:\.bind\(\s*this\s*\))?", e):
            e = re.sub(r"\.bind\(\s*this\s*\)$", "", e).replace("$this->", "this.").lstrip("$")
            h = self.s.handler(e, file)
            if h is not None and self.b.nodes[h].file != file and not self.imported(file, e.split(".")[-1]):
                h = None              # a same-named function elsewhere (a nested `def callback` here has no node)
        if h is None:
            m = re.fullmatch(r"\[\s*\$this\s*,\s*['\"](\w+)['\"]\s*\]", e)   # PHP [$this, 'method']
            if m:
                h = self.s.handler("this." + m.group(1), file)
        return h or self.fn_at(file, pos)

    def imported(self, file, name):
        src = self.s.text(file)
        n = re.escape(name)
        return bool(re.search(rf"^\s*from\s+[.\w]+\s+import\s+(?:\([^)]*\b{n}\b|[^\n]*\b{n}\b)|"
                              rf"\bimport\s*\{{[^}}]*\b{n}\b[^}}]*\}}\s*from|\b(?:const|let|var)\s*\{{[^}}]*\b{n}\b[^}}]*\}}\s*=\s*require|"
                              rf"\bimport\s+{n}\s+from|^use\s+[\w\\]+\\{n}\s*;", src, re.M))

    def enum(self, owner, member):
        if self._enums is None:
            self._enums = defaultdict(dict)
            for f in self.s.files:
                if not f.endswith(JS_EXT + (".py",)):
                    continue
                t = self.s.text(f)
                for e in re.finditer(r"\benum\s+(\w+)\s*\{([^{}]*)\}|\b(?:export\s+)?const\s+(\w+)\s*=\s*\{([^{}]*)\}\s*as\s+const", t):
                    name, body = (e.group(1), e.group(2)) if e.group(1) else (e.group(3), e.group(4))
                    for mm in re.finditer(r"(\w+)\s*[=:]\s*" + JSSTR, body):
                        self._enums[name].setdefault(mm.group(1), next(g for g in mm.groups()[1:] if g is not None))
                for e in re.finditer(r"^class\s+(\w+)\s*\([^)]*\b(?:str,\s*)?(?:Enum|StrEnum)\)\s*:\s*\n((?:[ \t]+[^\n]*\n?)+)", t, re.M):
                    for mm in re.finditer(r"^[ \t]+(\w+)\s*=\s*['\"]([^'\"\n]*)['\"]", e.group(2), re.M):
                        self._enums[e.group(1)].setdefault(mm.group(1), mm.group(2))
        return self._enums.get(owner, {}).get(member)

    def value(self, file, pos, expr, depth=0):
        """(name, confidence) of a topic / channel / key expression; parts that stay unknown become `{x}`
        placeholders (heuristic); (None, None) when nothing is known."""
        from .sockets import _top_split
        e = (expr or "").strip()
        e = re.sub(r"^(?:await\s+)", "", e)
        e = re.sub(r"\s+as\s+(?:string|const|any)\s*$", "", e)
        e = re.sub(r"!$", "", e).strip()
        if file.endswith(".rs"):
            e = _rs_str(e)
        if not e or depth > 4:
            return None, None
        while e.startswith("(") and e.endswith(")") and e.count("(") == e.count(")") and _bal(e[1:-1]):
            e = e[1:-1].strip()
        if file.endswith((".kt", ".kts")) and re.fullmatch(r'"[^"\n]*\$[^"\n]*"', e):
            return self._tpl(file, pos, e[1:-1], r"\$\{([^{}]*)\}|\$([A-Za-z_]\w*)", depth)
        lit = _strlit(e)
        if lit is not None:
            return lit, RESOLVED
        m = re.fullmatch(r'format!\s*\(\s*"([^"\n]*)"\s*((?:,[\s\S]*)?)\)', e)      # Rust format!("a.{}", x)
        if m:
            from .sockets import split_args
            args = split_args(m.group(2).lstrip(","))
            it = iter(args)
            body = re.sub(r"\{\}", lambda _m: "{" + next(it, "x").strip() + "}", m.group(1))
            return self._tpl(file, pos, body, r"\{([^{}:]*)(?::[^{}]*)?\}", depth)
        m = re.fullmatch(r'(?:System\.getenv|(?:std::)?env::var)\(\s*"(\w+)"\s*\)\s*(?:\?:\s*(.+)|\.unwrap_or(?:_else)?\(\s*(?:\|[^|]*\|\s*)?(.+)\))?', e, re.S)
        if m:
            d = m.group(2) or m.group(3)
            if d:
                v, _c = self.value(file, pos, d, depth + 1)
                if v is not None:
                    return v, HEURISTIC
            return f"env:{m.group(1)}", HEURISTIC
        # env with a default / without
        m = re.fullmatch(r"process\.env(?:\.(\w+)|\[\s*['\"](\w+)['\"]\s*\])\s*(?:\?\?|\|\|)\s*(.+)", e, re.S)
        if m:
            v, _c = self.value(file, pos, m.group(3), depth + 1)
            return (v, HEURISTIC) if v is not None else (f"env:{m.group(1) or m.group(2)}", HEURISTIC)
        m = re.fullmatch(r"process\.env(?:\.(\w+)|\[\s*['\"](\w+)['\"]\s*\])", e)
        if m:
            return f"env:{m.group(1) or m.group(2)}", HEURISTIC
        m = re.fullmatch(r"(?:os\.(?:environ\.get|getenv)|env|getenv|\$_ENV\.get)\(\s*['\"](\w+)['\"]\s*(?:,\s*(.+))?\)", e, re.S)
        if m:
            if m.group(2):
                v, _c = self.value(file, pos, m.group(2), depth + 1)
                if v is not None:
                    return v, HEURISTIC
            return f"env:{m.group(1)}", HEURISTIC
        m = re.fullmatch(r"os\.environ\[\s*['\"](\w+)['\"]\s*\]", e)
        if m:
            return f"env:{m.group(1)}", HEURISTIC
        if file.endswith((".kt", ".kts")) and "?:" in e:
            parts2 = _top_split(e, ("?:",))
            if len(parts2) == 2:
                v, _c = self.value(file, pos, parts2[1], depth + 1)
                if v is not None:
                    return v, HEURISTIC
        # conditional: one known branch
        parts = None
        m = re.fullmatch(r"(.+?)\s+if\s+.+?\s+else\s+(.+)", e, re.S) if file.endswith(".py") else None
        if m:
            parts = [m.group(1), m.group(2)]
        elif not file.endswith(".py"):
            t = _top_split(e, ("?",))
            if len(t) == 2 and ":" in t[1] and not t[1].startswith((".", "?")):
                br = _top_split(t[1], (":",))
                if len(br) == 2:
                    parts = br
        if parts:
            vals = [self.value(file, pos, p, depth + 1)[0] for p in parts]
            vals = [v for v in vals if v is not None and not _whole_ph(v)]
            if len(set(vals)) == 1:
                return vals[0], HEURISTIC
            return None, None
        # template literal / f-string / concatenation
        if e.startswith("`") and e.endswith("`"):
            return self._tpl(file, pos, e[1:-1], r"\$\{([^{}]*)\}", depth)
        m = re.fullmatch(r"[fF][rR]?(?:'([^'\n]*)'|\"([^\"\n]*)\")", e)
        if m:
            return self._tpl(file, pos, m.group(1) if m.group(1) is not None else m.group(2), r"\{([^{}]*)\}", depth)
        plus = _top_split(e, (" . ",) if file.endswith(".php") else ("+",))
        if len(plus) > 1:
            out, conf = [], RESOLVED
            for p in plus:
                v, c = self.value(file, pos, p, depth + 1)
                if v is None:
                    v, c = "{" + _ident(p) + "}", HEURISTIC
                out.append(v)
                conf = HEURISTIC if c == HEURISTIC else conf
            return "".join(out), conf
        # Enum.Member / CONST.member
        m = re.fullmatch(r"([A-Z]\w*)\.(\w+)(?:\.value)?", e)
        if m:
            v = self.enum(m.group(1), m.group(2))
            if v is not None:
                return v, RESOLVED
            if re.fullmatch(r"[A-Z][A-Z0-9_]{2,}", m.group(2)):       # Kotlin companion / Java static constant
                crx = re.compile(rf"\b(?:class|object|interface|enum|struct|impl)\s+{re.escape(m.group(1))}\b")
                own = [(f, v) for f, v in self.s.const_index().get(m.group(2), ()) if crx.search(self.s.text(f))]
                if len({v for _f, v in own}) == 1:
                    return self.value(own[0][0], 0, own[0][1].rstrip(",;").strip(), depth + 1)
                return self.ident(file, pos, m.group(2), field=False, depth=depth, globals_only=True)
        # PHP class constant self::X / Foo::X
        m = re.fullmatch(r"(?:self|static|[A-Z]\w*)::([A-Z][A-Z0-9_]*)", e)
        if m:
            mm = re.findall(rf"\bconst\s+{m.group(1)}\s*=\s*([^;\n]+);", self.s.text(file))
            if len(set(mm)) == 1:
                return self.value(file, pos, mm[0], depth + 1)
            return None, None
        # identifiers: locals, fields, module constants, imported constants
        m = re.fullmatch(r"(?:(this|self)\s*(?:\.|->)\s*|\$this->|\$)?([A-Za-z_]\w*)", e)
        if m:
            return self.ident(file, pos, m.group(2), field=bool(m.group(1)) or e.startswith("$this->"), depth=depth)
        m = re.fullmatch(r"(?:[\w.]*\.)?(?:settings|config|conf|Config|cfg)\.([A-Za-z_]\w*)", e)
        if m:
            name = m.group(1)
            if re.fullmatch(r"[A-Z][A-Z0-9_]{2,}", name):
                return self.ident(file, pos, name, field=False, depth=depth, globals_only=True)
            return self.settings_field(name, depth)
        return None, None


    def field_defaults(self):
        """Literal defaults of snake_case class fields (pydantic-settings / BaseModel / dataclass / attrs) (#157)."""
        if self._field_defs is not None:
            return self._field_defs
        out = defaultdict(list)
        # Indented class-body annotated assignment: `events_topic: str = "events"` / Field(default=...) / field(default=...)
        rx = re.compile(
            r"(?m)^[ \t]{1,8}([a-z_][a-z0-9_]*)\s*:\s*[^=\n]+=\s*(.+?)\s*(?:#.*)?$"
        )
        field_rx = re.compile(
            r"""^(?:(?:pydantic\.)?Field|(?:attr(?:\.ib|s)?\.)?ib|(?:dataclasses\.)?field|attrib)\s*\(\s*(?:default\s*=\s*)?"""
            + r"""(?:['"]([^'"]*)['"]|([A-Z][A-Z0-9_]{2,}))"""
        )
        for f in sorted(self.s.files):
            if not f.endswith(".py") or TEST_FILE.search(f) or "/tests/" in f.replace("\\", "/"):
                continue
            t = self.s.text(f)
            # Prefer settings / config modules; still accept any file that defines a Settings-like class
            interesting = bool(re.search(r"settings|config|conf", f, re.I)) or bool(
                re.search(r"\b(?:BaseSettings|BaseModel|dataclass|attrs\.define)\b|\bclass\s+(?:Settings|Config)\b", t)
            )
            if not interesting:
                continue
            for m in rx.finditer(t):
                # Skip method bodies: a `def` at the same or outer indent between class and this line is a method
                line_start = m.start()
                indent = len(m.group(0)) - len(m.group(0).lstrip())
                before = t[:line_start]
                # last `def`/`async def` at indent < field indent means we're inside a method
                last_def = None
                for dm in re.finditer(r"(?m)^([ \t]*)(?:async\s+)?def\s+", before):
                    last_def = dm
                if last_def is not None and len(last_def.group(1)) < indent and last_def.group(1) != "":
                    # def inside class (indent > 0) and field is more indented than def -> method body
                    if len(last_def.group(1)) > 0:
                        # check there's no class between def and field
                        mid = before[last_def.end():]
                        if not re.search(r"(?m)^[ \t]{0," + str(len(last_def.group(1)) + 1) + r"}class\s+", mid):
                            continue
                name, rhs = m.group(1), m.group(2).strip().rstrip(",")
                lit = _strlit(rhs)
                if lit is None:
                    fm = field_rx.match(rhs)
                    if fm:
                        lit = fm.group(1)
                        if lit is None and fm.group(2):
                            # Field(default=SOME_CONST) — resolve via const index later
                            out[name].append((f, fm.group(2), "const"))
                            continue
                if lit is not None:
                    out[name].append((f, lit, "lit"))
                else:
                    env = re.match(
                        r"""(?:os\.(?:environ\.get|getenv)|getenv)\(\s*['"](\w+)['"]\s*,\s*['"]([^'"]*)['"]""",
                        rhs,
                    )
                    if env:
                        out[name].append((f, env.group(2), "env"))
        self._field_defs = out
        return out

    def settings_field(self, name, depth):
        """Resolve `settings.<snake_case>` / `config.<snake_case>` from a class field default (#157)."""
        if depth > 4:
            return None, None
        cands = self.field_defaults().get(name, ())
        vals = {}
        for f, v, kind in cands:
            if kind == "const":
                r = self.ident(f, 0, v, field=False, depth=depth + 1, globals_only=True)
                if r[0] is not None:
                    vals[r[0]] = HEURISTIC if HEURISTIC in (vals.get(r[0]), r[1]) else r[1]
            elif kind == "env":
                vals[v] = HEURISTIC
            else:
                vals[v] = RESOLVED if vals.get(v) != HEURISTIC else HEURISTIC
        if len(vals) == 1:
            return next(iter(vals.items()))
        # Prefer settings/config path files when several modules disagree
        pref = {}
        for f, v, kind in cands:
            if not re.search(r"settings|config|conf", f, re.I):
                continue
            if kind == "lit":
                pref[v] = RESOLVED
            elif kind == "env":
                pref[v] = HEURISTIC
        if len(pref) == 1:
            return next(iter(pref.items()))
        return None, None

    def _tpl(self, file, pos, body, rx, depth):
        out, conf, last = [], RESOLVED, 0
        for m in re.finditer(rx, body):
            out.append(body[last:m.start()])
            g = m.group(1) if m.group(1) is not None else m.group(2)
            inner = re.sub(r"[!:][^{}]*$", "", g) if rx.startswith(r"\{") else g
            v, c = self.value(file, pos, inner, depth + 1)
            if v is None or _whole_ph(v):
                v, c = "{" + _ident(inner) + "}", HEURISTIC
            out.append(v)
            conf = HEURISTIC if c == HEURISTIC else conf
            last = m.end()
        out.append(body[last:])
        return "".join(out), conf

    def ident(self, file, pos, name, field, depth, globals_only=False):
        src = self.s.text(file)
        php = file.endswith(".php")
        if not globals_only:
            if field:
                cands = re.findall(rf"(?:this\.|self\.|\$this->){name}\s*(?::\s*[^=\n]+)?=(?!=)\s*([^;\n]+)", src)
                cands += re.findall(rf"^[ \t]*(?:(?:private|public|protected|readonly|static|const|final)\s+)*\$?{name}\s*(?::\s*[\w<>|\[\] ]+)?=(?!=)\s*([^;\n]+)", src, re.M)
                vals = {self.value(file, pos, c.rstrip(",").strip(), depth + 1) for c in cands}
                vals = {v for v in vals if v[0] is not None}
                if len(vals) == 1:
                    return next(iter(vals))
                if vals:
                    return None, None
            else:
                fn, lo, hi = self.s.fn_bounds(file, pos)
                var = (r"\$" if php else r"(?:\b(?:const|let|var|val|final)\s+)?(?<![.\w$])") + re.escape(name)
                rx = re.compile(var + r"\s*(?::\s*[^=\n]+?)?\s*=(?!=)\s*([^;\n]+)")
                if fn is not None:
                    found = [mm for mm in rx.finditer(src, lo, pos) if not self.s.masked(file, mm.start())]
                    if found:      # resolved where it is assigned (`topic = `${base}/${topic}`` reads the earlier one)
                        return self.value(file, found[-1].start(), found[-1].group(1).rstrip(",").strip(), depth + 1)
                    for p, d in self.s.params(file, fn):
                        if p == name:
                            return self.value(file, pos, d, depth + 1) if d else (None, None)
                # module level (outside functions)
                top = [mm for mm in rx.finditer(src) if self.s.enclosing(file, self.s.line_of(file, mm.start())) is None
                       and not self.s.masked(file, mm.start())]
                if top:
                    vals = {self.value(file, pos, mm.group(1).rstrip(",").strip(), depth + 1) for mm in top}
                    vals = {v for v in vals if v[0] is not None}
                    if len(vals) == 1:
                        return next(iter(vals))
        if re.fullmatch(r"[A-Z][A-Z0-9_]{2,}", name):
            vals = {}
            for f, v in self.s.const_index().get(name, ()):
                r = self.value(f, 0, v.rstrip(",;").strip(), depth + 1)
                if r[0] is not None:
                    vals[r[0]] = HEURISTIC if HEURISTIC in (vals.get(r[0]), r[1]) else r[1]
            if len(vals) == 1:
                return next(iter(vals.items()))
        return None, None

    # ---------------------------------------------------------------- endpoints
    def send(self, proto, name, conf, file, pos, lib, how, role="publish", **attrs):
        from .protocols import protocol_send
        if name is None or _whole_ph(name):
            self.miss(f"{proto}_send_unresolved", f"{file}:{self.s.line_of(file, pos)}")
            return
        if self.s.masked(file, pos):
            return
        fn = self.fn_at(file, pos)
        if fn is None:
            return
        line = self.s.line_of(file, pos)
        key = ("s", proto, name, fn, line)
        if key in self.done:
            return
        self.done.add(key)
        protocol_send(self.b, proto, name, fn, file, line, conf or RESOLVED, test=self.is_test(file, fn), role=role,
                      library=lib, how=how, **attrs)
        self.st[f"{proto}_sends"] += 1

    def recv(self, proto, name, conf, file, pos, handler, lib, how, **attrs):
        from .protocols import protocol_receive
        if name is None or _whole_ph(name) or re.match(r"(?:queue:)?\{[^{}]*\}[./:]", name):
            # unknown, or an unknown prefix (`{base_topic}/#`): as a wildcard it would take every topic
            self.miss(f"{proto}_receive_unresolved", f"{file}:{self.s.line_of(file, pos)} {name or ''}")
            return
        if self.s.masked(file, pos) or handler is None:
            return
        if self.is_test(file, handler):
            self.st["test_receivers_skipped"] += 1
            return
        line = self.s.line_of(file, pos)
        key = ("r", proto, name, handler, line)
        if key in self.done:
            return
        self.done.add(key)
        node_attrs = {k: attrs.pop(k) for k in ("exchange", "exchange_type") if k in attrs}
        protocol_receive(self.b, proto, name, handler, file, line, conf or RESOLVED, library=lib, how=how,
                         node_attrs=node_attrs, **attrs)
        self.st[f"{proto}_receivers"] += 1

    # ---------------------------------------------------------------- per file
    def libs_of(self, file, src):
        if file.endswith(JS_EXT):
            table, imp = JS_LIBS, r"""(?:from\s+|require\(\s*|import\s*\(\s*|import\s+)['"](?:{p})(?:/[\w./-]*)?['"]"""
        elif file.endswith(".py"):
            table, imp = PY_LIBS, r"^\s*(?:from\s+(?:{p})(?:\.[\w.]+)?\s+import|import\s+(?:{p})\b)"
        elif file.endswith(".php"):
            table, imp = PHP_LIBS, r"(?:{p})"
        elif file.endswith((".kt", ".kts")):
            table, imp = KT_LIBS, r"^\s*import\s+(?:{p})\b"
        elif file.endswith(".rs"):
            table, imp = RS_LIBS, r"^\s*(?:pub\s+)?use\s+(?:{p})\b|\b(?:{p})::"
        else:
            return set()
        return {k for k, p in table.items() if re.search(imp.replace("{p}", p), src, re.M)}

    JS_CLIENTS = [(r"mqtt\.connect(?:Async)?\s*\(|\bconnectAsync\s*\(", "mqtt"),
                  (r"new\s+(?:IORedis|Redis|Cluster)\s*\(|\bcreateClient\s*\(|\.duplicate\s*\(", "redis"),
                  (r"new\s+(?:PubSub|EventEmitter|Subject)\b", "local"),        # graphql-subscriptions: in-process
                  (r"\bnats\.connect\s*\(|\bjetstream\s*\(", "nats"),
                  (r"amqp\.connect\s*\(|\.createChannel\s*\(|\.createConfirmChannel\s*\(|amqp\.connect\s*\(", "amqp")]

    def js_clients(self, f, src, fl):
        """Variables / fields bound to a client of one library in this file (`const client = mqtt.connect(..)`)."""
        out = {}
        rules = list(self.JS_CLIENTS)
        imp = re.findall(r"""import\s*\{([^}]*)\}\s*from\s*['"]([^'"]+)['"]""", src)
        for names, mod in imp:
            for p, pat in JS_LIBS.items():
                if re.fullmatch(f"(?:{pat})(?:/[\\w./-]*)?", mod) and re.search(r"\bconnect\b", names) and p in ("nats", "mqtt", "amqp"):
                    rules.append((r"(?<![\w.])connect\s*\(", p))
        for m in re.finditer(r"(?:(?:const|let|var)\s+|this\.|\b)([\w$]+)\s*(?::[^=;\n]+)?=\s*(?:await\s+)?\(?\s*(?:await\s+)?([^;\n]+)", src):
            rhs = m.group(2)
            for pat, p in rules:
                if re.match(r"[\w$.]*?" + pat, rhs):
                    out.setdefault(m.group(1), p)
                    break
        return out

    def proto_for(self, file_libs, obj, candidates, clients=None, meth=None):
        """The protocol of a `<obj>.publish(` call: the one library of `candidates` the file imports, else the one the
        object name hints at (`this.redis`, `mqttClient`, `nc`) among the libraries the project uses."""
        c = (clients or {}).get((obj or "").split(".")[-1])
        if c:
            return c if c in candidates else None
        last = (obj or "").strip().split(".")[-1].lower()
        if last in ("this", "self", "$this", ""):
            return None                                       # the class's own publish()
        if meth and any(m == meth and (cls == last or cls.endswith(last) or last.endswith(cls)) for cls, m in self.own_methods):
            self.st["wrapper_calls_skipped"] += 1             # `this.mqtt.publish` -> the repo's class Mqtt (#35 part 2)
            return None
        here = [p for p in candidates if p in file_libs]
        if len(here) == 1:
            return here[0]
        pool = here or [p for p in candidates if p in self.used]
        hinted = [p for p in pool if re.search(HINTS[p], obj or "", re.I)]
        if len(hinted) == 1:
            return hinted[0]
        return None

    def run(self) -> dict:
        files = [f for f in sorted(self.s.files) if f.endswith(JS_EXT + (".py", ".php", ".kt", ".rs")) and not f.endswith(".d.ts")]
        libs = {}
        for f in files:
            src = self.s.text(f)
            libs[f] = self.libs_of(f, src)
            self.used |= libs[f]
        if not self.used:
            return {}
        for f in files:
            src = self.s.text(f)
            if f.endswith(JS_EXT):
                self.js_file(f, src, libs[f])
            elif f.endswith(".py"):
                self.py_file(f, src, libs[f])
            elif f.endswith(".kt"):
                self.kt_file(f, src, libs[f])
            elif f.endswith(".rs"):
                self.rs_file(f, src, libs[f])
            else:
                self.php_file(f, src, libs[f])
        self.finish_amqp()
        out = {k: v for k, v in self.st.items() if v}
        if not out:
            return {}
        out["libraries"] = sorted(self.used)
        if self.samples:
            out["samples"] = dict(self.samples)
        return out

    # ---------------------------------------------------------------- JS / TS
    def js_file(self, f, src, fl):
        rx = re.compile(r"((?:this\.)?[\w$.]*?)\s*\.\s*(send|sendBatch|produce|subscribe|run|publish|publishAsync|"
                        r"subscribeAsync|psubscribe|pSubscribe|request|xadd|xAdd|xreadgroup|xReadGroup|xread|xRead|"
                        r"sendToQueue|consume|bindQueue|assertExchange|on)\s*(?:<[^()]*?>)?\s*\(")
        handlers_on = {}                      # object -> handler of `.on('message', h)`
        for m in re.finditer(r"([\w$.]+)\s*\.\s*on\s*\(\s*['\"](message|pmessage|messageBuffer)['\"]\s*,\s*", src):
            a = _args(src, src.find("(", m.start(0) + len(m.group(1))))
            if len(a) >= 2 and not self.s.masked(f, m.start()):
                handlers_on[m.group(1)] = (a[1], m.start())
        any_on = next(iter(handlers_on.values())) if len(handlers_on) == 1 else None
        cl = self.js_clients(f, src, fl)
        for m in rx.finditer(src):
            obj, meth = m.group(1), m.group(2)
            if self.s.masked(f, m.start()) or not obj or meth == "on":
                continue
            a = _args(src, m.end() - 1)
            if not a:
                continue
            pos = m.start(2)
            if meth in ("send", "sendBatch") and "kafka" in self.used:
                if meth == "send" and _key(a[0], "topic") is not None and _key(a[0], "messages") is not None:
                    v, c = self.value(f, pos, _key(a[0], "topic"))
                    self.send("kafka", v, c, f, pos, "kafkajs", "producer.send()", role="produce")
                elif meth == "sendBatch":
                    tm = _key(a[0], "topicMessages") or ""
                    for t in re.finditer(r"\btopic\s*:\s*([^,}\n]+)", tm):
                        v, c = self.value(f, pos, t.group(1))
                        self.send("kafka", v, c, f, pos, "kafkajs", "producer.sendBatch()", role="produce")
            elif meth == "produce" and "kafka" in fl:
                v, c = self.value(f, pos, a[0])
                self.send("kafka", v, c, f, pos, "node-rdkafka", "producer.produce()", role="produce")
            elif meth == "subscribe" and "kafka" in self.used and a[0].startswith("{") and \
                    (_key(a[0], "topic") is not None or _key(a[0], "topics") is not None):
                self.js_kafka_sub(f, src, pos, obj, a[0])
            elif meth == "subscribe" and "kafka" in fl and a[0].startswith("[") and re.search(r"consumer", obj, re.I):
                for t in _list(a[0]):
                    v, c = self.value(f, pos, t)
                    self.recv("kafka", v, c, f, pos, self.fn_at(f, pos), "node-rdkafka", "consumer.subscribe()")
            elif meth in ("publish", "publishAsync") and len(a) >= 3 and self.proto_for(fl, obj, ("amqp",), cl, meth) == "amqp":
                ex, c1 = self.value(f, pos, a[0])
                key, c2 = self.value(f, pos, a[1])
                self.amqp_send(f, pos, ex, key, HEURISTIC if HEURISTIC in (c1, c2) else RESOLVED, "amqplib", "channel.publish()")
            elif meth == "sendToQueue" and self.proto_for(fl, obj, ("amqp",), cl, meth) == "amqp":
                q, c = self.value(f, pos, a[0])
                self.amqp_send(f, pos, "", q, c, "amqplib", "channel.sendToQueue()")
            elif meth == "consume" and self.proto_for(fl, obj, ("amqp",), cl, meth) == "amqp":
                qid = self.queue_id(f, pos, a[0])
                if qid:
                    h = self.handler(f, pos, a[1] if len(a) > 1 else None)
                    self.consumes.append((qid, h, f, pos, "amqplib", "channel.consume()"))
            elif meth == "bindQueue" and len(a) >= 2 and self.proto_for(fl, obj, ("amqp",), cl, meth) == "amqp":
                self.amqp_bind(f, pos, a[0], a[1], a[2] if len(a) > 2 else '""')
            elif meth == "assertExchange" and len(a) >= 2 and "amqp" in self.used:
                ex, _c = self.value(f, pos, a[0])
                t = _strlit(a[1])
                if ex is not None and t:
                    self.exch_type.setdefault(ex, t)
            elif meth in ("publish", "publishAsync", "request"):
                p = self.proto_for(fl, obj, PUBSUB if meth != "request" else ("nats",), cl, meth)
                if p is None:
                    continue
                v, c = self.value(f, pos, a[0])
                self.send("redis-pubsub" if p == "redis" else p, v, c, f, pos, self._lib(p, "js"),
                          f"{obj.split('.')[-1] or obj}.{meth}()", role="request" if meth == "request" else "publish")
            elif meth in ("subscribe", "subscribeAsync", "psubscribe", "pSubscribe"):
                p = self.proto_for(fl, obj, PUBSUB, cl, meth)
                if p is None:
                    continue
                self.js_pubsub_sub(f, src, pos, p, obj, meth, a, handlers_on.get(obj) or any_on)
            elif meth in ("xadd", "xAdd") and self.proto_for(fl, obj, ("redis",), cl, meth) == "redis":
                v, c = self.value(f, pos, a[0])
                self.send("redis-stream", v, c, f, pos, self._lib("redis", "js"), "XADD", role="publish")
            elif meth in ("xreadgroup", "xReadGroup", "xread", "xRead") and self.proto_for(fl, obj, ("redis",), cl, meth) == "redis":
                self.redis_stream_read(f, pos, a, meth)

    def _lib(self, p, lang):
        return {"js": {"redis": "ioredis / node-redis", "mqtt": "mqtt", "nats": "nats", "kafka": "kafkajs", "amqp": "amqplib"},
                "py": {"redis": "redis-py", "mqtt": "paho-mqtt / aiomqtt", "nats": "nats-py", "kafka": "kafka", "amqp": "pika"},
                "php": {"redis": "Laravel Redis / Predis", "mqtt": "php-mqtt", "amqp": "php-amqplib"},
                "kt": {"redis": "jedis / lettuce", "mqtt": "paho", "nats": "jnats"},
                "rs": {"redis": "redis-rs", "mqtt": "rumqttc", "nats": "async-nats"}}[lang].get(p, p)

    def js_kafka_sub(self, f, src, pos, obj, opt):
        topics = []
        if _key(opt, "topic") is not None:
            topics.append(_key(opt, "topic"))
        if _key(opt, "topics") is not None:
            topics += _list(_key(opt, "topics"))
        var = obj.split(".")[-1]
        run = None
        for r in re.finditer(r"([\w$.]+)\s*\.\s*run\s*\(\s*\{", src):
            if r.group(1).split(".")[-1] == var:
                run = r
        h = None
        if run:
            a = _args(src, src.find("(", run.start() + len(run.group(1))))
            cb = (_key(a[0], "eachMessage") or _key(a[0], "eachBatch")) if a else None
            h = self.handler(f, run.start(), cb if cb and not re.match(r"\s*(?:async\b|\(|function\b)", cb) else None)
        h = h or self.fn_at(f, pos)
        group = self.js_group(f, src, var)
        for t in topics:
            t = t.strip()
            rx = re.fullmatch(r"/(.+)/[gimsuy]*", t)
            if rx:
                g = _regex_glob(rx.group(1))
                if g is None:
                    self.miss("kafka_regex_subscription", f"{f}:{self.s.line_of(f, pos)} {t[:40]}")
                    continue
                v, c = g, HEURISTIC
            else:
                v, c = self.value(f, pos, t)
            self.recv("kafka", v, c, f, pos, h, "kafkajs", "consumer.subscribe()", group=group)

    def js_group(self, f, src, var):
        for g in re.finditer(r"([\w$]+)\s*(?::[^=\n]+)?=\s*(?:await\s+)?[\w$.]*\.consumer\s*\(\s*\{[^{}]*?\bgroupId\s*:\s*([^,}\n]+)", src):
            if g.group(1) == var:
                return self.value(f, g.start(), g.group(2))[0]
        allg = re.findall(r"\bgroupId\s*:\s*([^,}\n]+)", src)
        if len(allg) == 1:
            return self.value(f, 0, allg[0])[0]
        return None

    def js_pubsub_sub(self, f, src, pos, p, obj, meth, a, on):
        names, cb = [], None
        for x in a:
            if re.match(r"\s*(?:async\b|\(|function\b|[\w$]+\s*=>)", x) or (x.strip() and not _strlit(x) and
                                                                           re.fullmatch(r"(?:this\.)?[\w$.]+", x.strip())
                                                                           and x is a[-1] and len(a) > 1 and p != "nats"):
                cb = x
                continue
            if x.strip().startswith("{") and p != "nats":
                if p == "mqtt":                       # {topic: {qos}}
                    names += [k.strip() for k in re.findall(r"""(['"][^'"]+['"])\s*:""", x)]
                continue
            if x.strip().startswith("{") and p == "nats":
                cb = _key(x, "callback") or cb
                continue
            names += _list(x)
        group = None
        if p == "nats" and len(a) > 1 and a[1].strip().startswith("{"):
            q = _key(a[1], "queue")
            group = self.value(f, pos, q)[0] if q else None
        if cb is None and on is not None:
            cb = on[0]
        h = self.handler(f, pos, cb if cb and not re.match(r"\s*(?:async\b|\(|function\b|[\w$]+\s*=>)", cb) else None)
        proto = "redis-pubsub" if p == "redis" else p
        for n in names:
            v, c = self.value(f, pos, n)
            if v is None and n.strip().startswith(("[", "...")):
                continue
            self.recv(proto, v, c, f, pos, h, self._lib(p, "js"), f"{obj.split('.')[-1] or obj}.{meth}()", group=group)

    # ---------------------------------------------------------------- Python
    def py_file(self, f, src, fl):
        if not fl and not any(re.search(HINTS[p], src, re.I) for p in self.used if p in ("redis", "kafka")):
            return
        rx = re.compile(r"((?:self\.)?[\w.]*?)\s*\.\s*(produce|send|send_and_wait|subscribe|psubscribe|publish|request|"
                        r"basic_publish|basic_consume|queue_bind|exchange_declare|declare_exchange|bind|consume|"
                        r"message_callback_add|xadd|xreadgroup|xread|pull_subscribe)\s*\(")
        vars_ = self.py_vars(f, src)
        for m in rx.finditer(src):
            obj, meth = m.group(1), m.group(2)
            if self.s.masked(f, m.start()) or not obj:
                continue
            a = _args(src, m.end() - 1)
            pos = m.start(2)
            kind = vars_.get(obj.split(".")[-1]) or vars_.get(obj)
            pos_args = [x for x in a if not re.match(r"\s*\*?\*?\w+\s*=(?!=)", x)]
            if meth in ("produce", "send", "send_and_wait") and "kafka" in fl and \
                    (kind == "kafka_producer" or (kind is None and re.search(r"produc", obj, re.I))):
                t = _kw(a, "topic") or (pos_args[0] if pos_args else None)
                v, c = self.value(f, pos, t)
                self.send("kafka", v, c, f, pos, "kafka", f"producer.{meth}()", role="produce")
            elif meth == "send" and kind == "faust_topic":
                v = vars_.get(("topic", obj.split(".")[-1]))
                self.send("kafka", v, RESOLVED, f, pos, "faust", "topic.send()", role="produce")
            elif meth == "subscribe" and "kafka" in fl and (kind == "kafka_consumer" or (kind is None and re.search(r"consum", obj, re.I))):
                ts = _kw(a, "topics") or (pos_args[0] if pos_args else None)
                for t in _list(ts):
                    v, c = self.value(f, pos, t)
                    self.recv("kafka", v, c, f, pos, self.fn_at(f, pos), "kafka", "consumer.subscribe()",
                              group=self.py_group(f, src))
            elif meth == "basic_publish" and "amqp" in fl:
                ex = _kw(a, "exchange") if _kw(a, "exchange") is not None else (pos_args[0] if pos_args else None)
                key = _kw(a, "routing_key") if _kw(a, "routing_key") is not None else (pos_args[1] if len(pos_args) > 1 else None)
                e1, c1 = self.value(f, pos, ex)
                k1, c2 = self.value(f, pos, key)
                self.amqp_send(f, pos, e1, k1, HEURISTIC if HEURISTIC in (c1, c2) else RESOLVED, "pika", "basic_publish()")
            elif meth == "publish" and "amqp" in fl and (_kw(a, "routing_key") is not None):
                k1, c2 = self.value(f, pos, _kw(a, "routing_key"))
                if obj.endswith("default_exchange"):
                    e1, c1 = "", RESOLVED
                else:
                    e1, c1 = vars_.get(("exchange", obj.split(".")[-1])), RESOLVED
                    if e1 is None:
                        self.miss("amqp_exchange_unresolved", f"{f}:{self.s.line_of(f, pos)} {obj}")
                        continue
                self.amqp_send(f, pos, e1, k1, c2, "aio-pika", "exchange.publish()")
            elif meth == "basic_consume" and "amqp" in fl:
                q = _kw(a, "queue")
                cb = _kw(a, "on_message_callback") or _kw(a, "consumer_callback")
                if q is None and pos_args:
                    if len(pos_args) >= 2 and _strlit(pos_args[0]) is None and re.fullmatch(r"[\w.]+", pos_args[0]) \
                            and not re.search(r"queue", pos_args[0]):
                        cb, q = cb or pos_args[0], pos_args[1]            # pika 0.x basic_consume(callback, queue)
                    else:
                        q = pos_args[0]
                        cb = cb or (pos_args[1] if len(pos_args) > 1 else None)
                if q is not None and cb is None and len(pos_args) == 1 and _kw(a, "queue") is not None:
                    cb = pos_args[0]
                qid = self.queue_id(f, pos, q)
                if qid:
                    self.consumes.append((qid, self.handler(f, pos, cb), f, pos, "pika", "basic_consume()"))
            elif meth == "consume" and "amqp" in fl and kind == "amqp_queue":
                qid = self.queue_id(f, pos, obj.split(".")[-1])
                if qid:
                    self.consumes.append((qid, self.handler(f, pos, pos_args[0] if pos_args else _kw(a, "callback")), f, pos,
                                          "aio-pika", "queue.consume()"))
            elif meth == "queue_bind" and "amqp" in fl:
                q = _kw(a, "queue") or (pos_args[0] if pos_args else None)
                ex = _kw(a, "exchange") or (pos_args[1] if len(pos_args) > 1 else None)
                key = _kw(a, "routing_key") or (pos_args[2] if len(pos_args) > 2 else '""')
                if q and ex:
                    self.amqp_bind(f, pos, q, ex, key)
            elif meth == "bind" and "amqp" in fl and kind == "amqp_queue":
                ex = pos_args[0] if pos_args else _kw(a, "exchange")
                key = _kw(a, "routing_key") or (pos_args[1] if len(pos_args) > 1 else '""')
                exv = vars_.get(("exchange", (ex or "").strip()))
                self.amqp_bind(f, pos, obj.split(".")[-1], repr(exv) if exv is not None else ex, key)
            elif meth in ("exchange_declare", "declare_exchange") and "amqp" in fl:
                ex = _kw(a, "exchange") or _kw(a, "name") or (pos_args[0] if pos_args else None)
                t = _kw(a, "exchange_type") or _kw(a, "type") or (pos_args[1] if len(pos_args) > 1 else None)
                exv, _c = self.value(f, pos, ex)
                tv = _strlit(t) if t else None
                if t and tv is None:
                    mm = re.search(r"ExchangeType\.(\w+)", t)
                    tv = mm.group(1).lower() if mm else None
                if exv is not None:
                    self.exch_type.setdefault(exv, tv or ("direct" if meth == "declare_exchange" else "direct"))
            elif meth in ("publish", "request"):
                p = self.py_proto(fl, obj, kind, ("nats",) if meth == "request" else PUBSUB, meth)
                if p is None:
                    continue
                t = _kw(a, "topic") or _kw(a, "channel") or _kw(a, "subject") or (pos_args[0] if pos_args else None)
                v, c = self.value(f, pos, t)
                self.send("redis-pubsub" if p == "redis" else p, v, c, f, pos, self._lib(p, "py"),
                          f"{obj.split('.')[-1] or obj}.{meth}()", role="request" if meth == "request" else "publish")
            elif meth in ("subscribe", "psubscribe", "message_callback_add", "pull_subscribe"):
                p = self.py_proto(fl, obj, kind, PUBSUB, meth)
                if p is None:
                    continue
                self.py_pubsub_sub(f, src, pos, p, obj, meth, a, pos_args)
            elif meth == "xadd" and self.py_proto(fl, obj, kind, ("redis",), meth) == "redis":
                v, c = self.value(f, pos, _kw(a, "name") or (pos_args[0] if pos_args else None))
                self.send("redis-stream", v, c, f, pos, "redis-py", "XADD")
            elif meth in ("xreadgroup", "xread") and self.py_proto(fl, obj, kind, ("redis",), meth) == "redis":
                self.redis_stream_read(f, pos, a, meth)

    def py_proto(self, fl, obj, kind, cands, meth=None):
        if kind in ("redis", "mqtt", "nats"):
            return kind if kind in cands else None
        if kind:
            return None
        return self.proto_for(fl, obj, cands, None, meth)

    def py_vars(self, f, src):
        """Variables / attributes bound to a client of one library in this file."""
        out = {}
        rules = [(r"(?:Kafka|AIOKafka|Serializing)?Producer\s*\(", "kafka_producer"),
                 (r"(?:Kafka|AIOKafka|Deserializing)?Consumer\s*\(", "kafka_consumer"),
                 (r"(?:redis\.)?(?:Strict)?Redis(?:Cluster)?\s*(?:\.from_url)?\s*\(|redis\.from_url\s*\(|aioredis\.\w+\s*\(|\.pubsub\s*\(", "redis"),
                 (r"(?:mqtt\.|paho\.mqtt\.client\.)?Client\s*\(|aiomqtt\.Client\s*\(|MQTTClient\s*\(", "mqtt"),
                 (r"(?:nats\.connect|NATS\s*\(|\.jetstream\s*\()", "nats"),
                 (r"(?:\w+\.)?declare_queue\s*\(", "amqp_queue"),
                 (r"(?:\w+\.)?app\.topic\s*\(|\bapp\.topic\s*\(", "faust_topic")]
        imports = self.libs_of(f, src)
        for m in re.finditer(r"(?:(?:self\.)?(\w+)[ \t]*(?::[ \t]*[\w.\[\]| ]+)?[ \t]*=[ \t]*(?:await[ \t]+)?|async\s+with\s+(?:await\s+)?|with\s+)"
                             r"([\w.]+\s*\([^\n]*)", src):
            rhs = m.group(2)
            for pat, kind in rules:
                if re.match(pat, rhs) or re.match(r"[\w.]+\." + pat, rhs):
                    if kind == "mqtt" and "mqtt" not in imports:
                        continue
                    if kind in ("kafka_producer", "kafka_consumer") and "kafka" not in imports:
                        continue
                    if kind == "redis" and "redis" not in imports:
                        continue
                    var = m.group(1)
                    if var is None:
                        w = re.search(r"\bas\s+(\w+)\s*:", src[m.end():m.end() + 300])
                        var = w.group(1) if w else None
                    if var and var not in ("None", "True", "False"):
                        out[var] = kind
                        if kind == "faust_topic":
                            a = _args(rhs, rhs.find("("))
                            v, _c = self.value(f, m.start(), a[0]) if a else (None, None)
                            if v is not None:
                                out[("topic", var)] = v
                    break
        for m in re.finditer(r"(?:self\.)?(\w+)\s*=\s*(?:await\s+)?[\w.]+\.declare_exchange\s*\(", src):
            a = _args(src, src.find("(", m.end() - 1))
            ex = (_kw(a, "name") or (a[0] if a else None))
            v, _c = self.value(f, m.start(), ex)
            if v is not None:
                out[("exchange", m.group(1))] = v
        return out

    def py_group(self, f, src):
        gs = set(re.findall(r"""\bgroup_id\s*=\s*([^,)\n]+)|['"]group\.id['"]\s*:\s*([^,}\n]+)""", src))
        vals = {self.value(f, 0, a or b)[0] for a, b in gs}
        vals.discard(None)
        return next(iter(vals)) if len(vals) == 1 else None

    def py_pubsub_sub(self, f, src, pos, p, obj, meth, a, pos_args):
        proto = "redis-pubsub" if p == "redis" else p
        how = f"{obj.split('.')[-1] or obj}.{meth}()"
        lib = self._lib(p, "py")
        if meth == "message_callback_add":
            v, c = self.value(f, pos, pos_args[0] if pos_args else None)
            self.recv(proto, v, c, f, pos, self.handler(f, pos, pos_args[1] if len(pos_args) > 1 else None), lib, how)
            return
        if p == "redis":
            for x in a:
                mm = re.match(r"\s*\*\*\s*(\{[\s\S]*\})\s*$", x)
                if mm:
                    for k, h in re.findall(r"""(['"][^'"]+['"])\s*:\s*([\w.]+)""", mm.group(1)):
                        v, c = self.value(f, pos, k)
                        self.recv(proto, v, c, f, pos, self.handler(f, pos, h), lib, how)
                    continue
                kv = re.match(r"\s*(\w+)\s*=\s*([\w.]+)\s*$", x)
                if kv:
                    self.recv(proto, kv.group(1), RESOLVED, f, pos, self.handler(f, pos, kv.group(2)), lib, how)
                    continue
                for n in _list(x):
                    v, c = self.value(f, pos, n)
                    self.recv(proto, v, c, f, pos, self.fn_at(f, pos), lib, how)
            return
        if p == "nats":
            v, c = self.value(f, pos, _kw(a, "subject") or (pos_args[0] if pos_args else None))
            q = _kw(a, "queue") or _kw(a, "durable")
            self.recv(proto, v, c, f, pos, self.handler(f, pos, _kw(a, "cb")), lib, how,
                      group=self.value(f, pos, q)[0] if q else None)
            return
        # MQTT: subscribe("t") / subscribe(("t", 0)) / subscribe([("a", 0), ("b", 1)]); handler: on_message
        t = _kw(a, "topic") or (pos_args[0] if pos_args else None)
        items = _list(t)
        if len(items) == 2 and re.fullmatch(r"\d", items[1].strip()):
            items = [items[0]]
        cb = None
        om = re.findall(r"\.on_message\s*=\s*([\w.]+)", src)
        if len(set(om)) == 1:
            cb = om[0]
        for it in items:
            it = it.strip()
            if it.startswith("("):
                it = _list(it)[0]
            v, c = self.value(f, pos, it)
            self.recv(proto, v, c, f, pos, self.handler(f, pos, cb), lib, how)

    # ---------------------------------------------------------------- PHP
    def php_file(self, f, src, fl):
        if not fl:
            return
        rx = re.compile(r"(?:(\$\w+(?:\s*->\s*\w+(?:\([^()]*\))?)*?)\s*->\s*|(Redis::(?:connection\([^()]*\)\s*->\s*)?))"
                        r"(basic_publish|basic_consume|queue_bind|exchange_declare|publish|subscribe|psubscribe)\s*\(")
        for m in rx.finditer(src):
            obj, meth = m.group(1) or m.group(2), m.group(3)
            if self.s.masked(f, m.start()):
                continue
            a = _args(src, m.end() - 1)
            pos = m.start(3)
            if meth == "basic_publish" and "amqp" in fl:
                e1, c1 = self.value(f, pos, a[1] if len(a) > 1 else '""')
                k1, c2 = self.value(f, pos, a[2] if len(a) > 2 else '""')
                self.amqp_send(f, pos, e1, k1, HEURISTIC if HEURISTIC in (c1, c2) else RESOLVED, "php-amqplib", "basic_publish()")
            elif meth == "basic_consume" and "amqp" in fl and a:
                qid = self.queue_id(f, pos, a[0])
                cb = a[6] if len(a) > 6 else None
                if qid:
                    self.consumes.append((qid, self.handler(f, pos, cb), f, pos, "php-amqplib", "basic_consume()"))
            elif meth == "queue_bind" and "amqp" in fl and len(a) >= 2:
                self.amqp_bind(f, pos, a[0], a[1], a[2] if len(a) > 2 else '""')
            elif meth == "exchange_declare" and "amqp" in fl and len(a) >= 2:
                ex, _c = self.value(f, pos, a[0])
                t = _strlit(a[1])
                if ex is not None and t:
                    self.exch_type.setdefault(ex, t)
            elif meth == "publish" and a:
                p = "redis" if obj.startswith("Redis::") or ("redis" in fl and re.search(r"redis", obj, re.I)) else \
                    "mqtt" if "mqtt" in fl and re.search(r"mqtt|client", obj, re.I) else None
                if p is None and fl & {"redis", "mqtt"} and len(fl & {"redis", "mqtt"}) == 1 and "amqp" not in fl:
                    p = next(iter(fl & {"redis", "mqtt"}))
                if p:
                    v, c = self.value(f, pos, a[0])
                    self.send("redis-pubsub" if p == "redis" else "mqtt", v, c, f, pos, self._lib(p, "php"), f"{meth}()")
            elif meth in ("subscribe", "psubscribe") and a:
                p = "redis" if obj.startswith("Redis::") or ("redis" in fl and re.search(r"redis", obj, re.I)) else \
                    "mqtt" if "mqtt" in fl else None
                if p is None:
                    continue
                cb = a[1] if len(a) > 1 else None
                h = self.handler(f, pos, cb if cb and not re.match(r"\s*(?:function|fn|static)\b", cb) else None)
                for n in _list(a[0]):
                    v, c = self.value(f, pos, n)
                    self.recv("redis-pubsub" if p == "redis" else "mqtt", v, c, f, pos, h, self._lib(p, "php"), f"{meth}()")

    # ---------------------------------------------------------------- Kotlin (JVM clients, Spring)
    def kt_file(self, f, src, fl):
        if not fl:
            return
        # Spring listeners: @KafkaListener(topics = ["a", "b"], groupId = "g"), @RabbitListener(queues = ["q"]),
        # @RabbitListener(bindings = [QueueBinding(value = Queue("q"), exchange = Exchange("ex"), key = ["k"])])
        for m in re.finditer(r"@(KafkaListener|RabbitListener|JmsListener|SqsListener)\s*\(", src):
            if self.s.masked(f, m.start()):
                continue
            a = _args(src, m.end() - 1)
            h = self.annotated_fn(f, src, m.end())
            if h is None:
                continue
            if m.group(1) == "KafkaListener" and "kafka" in fl:
                ts = _kw(a, "topics") or _kw(a, "topicPattern") or (a[0] if a and "=" not in a[0] else None)
                g = _kw(a, "groupId")
                for t in _list(_kt_arr(ts)):
                    v, c = self.value(f, m.start(), t)
                    if _kw(a, "topicPattern") and v:
                        v, c = _regex_glob(v), HEURISTIC
                    self.recv("kafka", v, c, f, m.start(), h, "spring-kafka", "@KafkaListener",
                              group=self.value(f, m.start(), g)[0] if g else None)
            elif m.group(1) == "RabbitListener" and "amqp" in fl:
                qs = _kw(a, "queues") or _kw(a, "queuesToDeclare")
                for q in _list(_kt_arr(qs)):
                    q = re.sub(r"^Queue\s*\(\s*(?:value\s*=\s*|name\s*=\s*)?([^,)]*)[\s\S]*\)$", r"\1", q.strip())
                    qid = self.queue_id(f, m.start(), q)
                    if qid:
                        self.consumes.append((qid, h, f, m.start(), "spring-amqp", "@RabbitListener"))
                for qb in re.finditer(r"QueueBinding\s*\(", _kw(a, "bindings") or ""):
                    body = _kw(a, "bindings")
                    ba = _args(body, qb.end() - 1)
                    qv = re.search(r"Queue\s*\(\s*(?:value\s*=\s*|name\s*=\s*)?([^,)]*)", _kw(ba, "value") or "")
                    ex = re.search(r"Exchange\s*\(\s*(?:value\s*=\s*|name\s*=\s*)?([^,)]*)(?:[^)]*type\s*=\s*(?:ExchangeTypes\.)?(\w+|\"\w+\"))?",
                                   _kw(ba, "exchange") or "")
                    keys = _list(_kt_arr(_kw(ba, "key"))) if _kw(ba, "key") else ['""']
                    qname = qv.group(1).strip() if qv and qv.group(1).strip() else None
                    qid = self.queue_id(f, m.start(), qname) if qname else f"anon:{f}:{self.s.line_of(f, m.start())}"
                    if not ex or not qid:
                        continue
                    exv, _c = self.value(f, m.start(), ex.group(1))
                    if exv is not None and ex.group(2):
                        self.exch_type.setdefault(exv, ex.group(2).strip('"').lower())
                    for k in keys:
                        self.amqp_bind_id(f, m.start(), qid, exv, k)
                    self.consumes.append((qid, h, f, m.start(), "spring-amqp", "@RabbitListener"))
        # `x.subscribe(..)`, or a bare `subscribe(..)` inside `KafkaConsumer(props).apply { .. }` / `with(channel) { .. }`
        rx = re.compile(r"(?:((?:this\.)?[\w.]*?)\s*(?:\?|!!)?\.\s*|(?<![\w.$]))(send|subscribe|basicPublish|basicConsume|"
                        r"queueBind|exchangeDeclare|convertAndSend|publish|request|xadd)\s*(?:<[^()]*?>)?\s*\(")
        cl = self.kt_clients(f, src)
        for m in rx.finditer(src):
            obj, meth = m.group(1), m.group(2)
            if self.s.masked(f, m.start()):
                continue
            if obj is None:
                if re.search(r"\bfun\s+(?:<[^>]*>\s*)?(?:\w+\.)?$", src[max(0, m.start() - 40):m.start()]):
                    continue                      # a declaration, not a call
                obj = ""
            a = _args(src, m.end() - 1)
            pos = m.start(2)
            last = obj.split(".")[-1] if obj else ""
            kind = cl.get(last)
            if meth == "send" and "kafka" in fl and (kind == "kafka_producer" or re.search(r"produc|kafkaTemplate|template", obj, re.I)):
                if not a:
                    continue
                r = re.match(r"\s*ProducerRecord\s*(?:<[^()]*?>)?\s*\(", a[0])
                t = _args(a[0], a[0].index("(", r.end() - 1))[0] if r else a[0]
                v, c = self.value(f, pos, t)
                self.send("kafka", v, c, f, pos, "kafka-clients" if r else "spring-kafka", f"{last}.send()", role="produce")
            elif meth == "subscribe" and "kafka" in fl and (kind == "kafka_consumer" or (not obj and "KafkaConsumer" in src)):
                if a:
                    for t in _list(_kt_arr(a[0])):
                        v, c = self.value(f, pos, t)
                        self.recv("kafka", v, c, f, pos, self.fn_at(f, pos), "kafka-clients", "consumer.subscribe()",
                                  group=self.kt_group(f, src))
            elif meth == "basicPublish" and "amqp" in fl and len(a) >= 2:
                named = _kw(a, "exchange") is not None or _kw(a, "routingKey") is not None
                e1, c1 = self.value(f, pos, _kw(a, "exchange") if named else a[0]) if not named or _kw(a, "exchange") is not None else ("", RESOLVED)
                k1, c2 = self.value(f, pos, _kw(a, "routingKey") if named else a[1])
                self.amqp_send(f, pos, e1, k1, HEURISTIC if HEURISTIC in (c1, c2) else RESOLVED, "amqp-client", "basicPublish()")
            elif meth == "convertAndSend" and "amqp" in fl and len(a) >= 2:
                if len(a) >= 3:
                    e1, c1 = self.value(f, pos, a[0])
                    k1, c2 = self.value(f, pos, a[1])
                else:
                    e1, c1 = "", RESOLVED
                    k1, c2 = self.value(f, pos, a[0])
                self.amqp_send(f, pos, e1, k1, HEURISTIC if HEURISTIC in (c1, c2) else RESOLVED, "spring-amqp", "convertAndSend()")
            elif meth == "basicConsume" and "amqp" in fl and a:
                qid = self.queue_id(f, pos, _kw(a, "queue") or a[0])
                if qid:
                    cb = next((x for x in a[1:] if re.fullmatch(r"\w+", x.strip()) and x.strip() not in ("true", "false")), None)
                    self.consumes.append((qid, self.handler(f, pos, cb), f, pos, "amqp-client", "basicConsume()"))
            elif meth == "queueBind" and "amqp" in fl and len(a) >= 2:
                pa = [x for x in a if not re.match(r"\s*\w+\s*=(?!=)", x)]
                self.amqp_bind(f, pos, _kw(a, "queue") or pa[0], _kw(a, "exchange") or pa[1],
                               _kw(a, "routingKey") or (pa[2] if len(pa) > 2 else '""'))
            elif meth == "exchangeDeclare" and "amqp" in fl and len(a) >= 2:
                ex, _c = self.value(f, pos, _kw(a, "name") or _kw(a, "exchange") or a[0])
                t = _strlit(a[1]) or (re.search(r"BuiltinExchangeType\.(\w+)", a[1]).group(1).lower()
                                      if re.search(r"BuiltinExchangeType\.(\w+)", a[1]) else None)
                if ex is not None and t:
                    self.exch_type.setdefault(ex, t)
            elif meth in ("publish", "request", "subscribe") and kind in ("mqtt", "nats", "redis"):
                if not a:
                    continue
                v, c = self.value(f, pos, a[0])
                proto = "redis-pubsub" if kind == "redis" else kind
                if meth == "subscribe":
                    self.recv(proto, v, c, f, pos, self.fn_at(f, pos), self._lib(kind, "kt"), f"{last}.subscribe()")
                else:
                    self.send(proto, v, c, f, pos, self._lib(kind, "kt"), f"{last}.{meth}()",
                              role="request" if meth == "request" else "publish")

    def annotated_fn(self, f, src, at):
        """The function declared after an annotation ending near `at`."""
        m = re.compile(r"[\s\S]{0,400}?\bfun\s+(?:<[^>]*>\s*)?(\w+)").match(src, at)
        if not m:
            return None
        line = self.s.line_of(f, m.start(1))
        e = self.s.enclosing(f, line)
        return e[1] if e and self.b.nodes[e[1]].line == line else (e[1] if e else None)

    def kt_clients(self, f, src):
        out = {}
        rules = [(r"KafkaProducer\b", "kafka_producer"), (r"KafkaConsumer\b", "kafka_consumer"),
                 (r"(?:MqttClient|MqttAsyncClient)\s*\(|Mqtt[35]?Client\.builder", "mqtt"),
                 (r"Nats\.connect\s*\(|\.createDispatcher\s*\(", "nats"),
                 (r"Jedis(?:Pool)?\s*\(|RedisClient\.create\s*\(", "redis")]
        for m in re.finditer(r"\b(?:val|var)\s+(\w+)\s*(?::\s*([\w<>?, .]+?))?\s*=\s*([^\n]+)", src):
            text = (m.group(2) or "") + " " + m.group(3)
            for pat, kind in rules:
                if re.search(pat, text):
                    out.setdefault(m.group(1), kind)
                    break
        for m in re.finditer(r"\b(?:val|var)\s+(\w+)\s*:\s*(KafkaTemplate|RabbitTemplate|StringRedisTemplate|RedisTemplate)\b", src):
            out.setdefault(m.group(1), {"KafkaTemplate": "kafka_producer"}.get(m.group(2), "template"))
        return out

    def kt_group(self, f, src):
        g = re.findall(r"(?:GROUP_ID_CONFIG\]?\s*[=,]\s*|\"group\.id\"\s*(?:\]\s*=|,|to)\s*)([^\n,)]+)", src)
        vals = {self.value(f, 0, x.strip())[0] for x in g}
        vals.discard(None)
        return next(iter(vals)) if len(vals) == 1 else None

    # ---------------------------------------------------------------- Rust
    def rs_file(self, f, src, fl):
        if not fl:
            return
        rx = re.compile(r"\.\s*(basic_publish|basic_consume|queue_bind|exchange_declare|subscribe|queue_subscribe|publish|"
                        r"request|psubscribe|xadd|send)\s*(?:::<[^()]*?>)?\s*\(")
        for m in rx.finditer(src):
            meth = m.group(1)
            if self.s.masked(f, m.start()):
                continue
            a = _args(src, m.end() - 1)
            pos = m.start(1)
            if meth == "basic_publish" and "amqp" in fl and len(a) >= 2:
                e1, c1 = self.value(f, pos, _rs_str(a[0]))
                k1, c2 = self.value(f, pos, _rs_str(a[1]))
                self.amqp_send(f, pos, e1, k1, HEURISTIC if HEURISTIC in (c1, c2) else RESOLVED, "lapin", "basic_publish()")
            elif meth == "basic_consume" and "amqp" in fl and a:
                qid = self.queue_id(f, pos, _rs_str(a[0]))
                if qid:
                    self.consumes.append((qid, self.fn_at(f, pos), f, pos, "lapin", "basic_consume()"))
            elif meth == "queue_bind" and "amqp" in fl and len(a) >= 2:
                self.amqp_bind(f, pos, _rs_str(a[0]), _rs_str(a[1]), _rs_str(a[2]) if len(a) > 2 else '""')
            elif meth == "exchange_declare" and "amqp" in fl and len(a) >= 2:
                ex, _c = self.value(f, pos, _rs_str(a[0]))
                t = re.search(r"ExchangeKind::(\w+)", a[1])
                if ex is not None and t:
                    self.exch_type.setdefault(ex, t.group(1).lower())
            elif meth == "send" and "kafka" in fl and a and re.match(r"\s*(?:&\s*)?(?:Future|Base)Record::", a[0]):
                t = re.search(r"Record::(?:<[^()]*>)?\s*(?:::)?to\s*\(\s*([^)]*)\)", a[0])
                if t:
                    v, c = self.value(f, pos, _rs_str(t.group(1)))
                    self.send("kafka", v, c, f, pos, "rdkafka", "producer.send()", role="produce")
            elif meth == "subscribe" and "kafka" in fl and a and a[0].strip().startswith(("&[", "[")):
                for t in _list(a[0].strip().lstrip("&")):
                    v, c = self.value(f, pos, _rs_str(t))
                    self.recv("kafka", v, c, f, pos, self.fn_at(f, pos), "rdkafka", "consumer.subscribe()")
            elif meth in ("publish", "request", "subscribe", "queue_subscribe", "psubscribe"):
                p = [x for x in ("nats", "mqtt", "redis") if x in fl]
                if len(p) != 1 or not a:
                    continue
                p = p[0]
                v, c = self.value(f, pos, _rs_str(a[0]))
                proto = "redis-pubsub" if p == "redis" else p
                if meth in ("subscribe", "queue_subscribe", "psubscribe"):
                    g = self.value(f, pos, _rs_str(a[1]))[0] if meth == "queue_subscribe" and len(a) > 1 else None
                    self.recv(proto, v, c, f, pos, self.fn_at(f, pos), self._lib(p, "rs"), f"{meth}()", group=g)
                else:
                    self.send(proto, v, c, f, pos, self._lib(p, "rs"), f"{meth}()", role="request" if meth == "request" else "publish")
            elif meth == "xadd" and "redis" in fl and a:
                v, c = self.value(f, pos, _rs_str(a[0]))
                self.send("redis-stream", v, c, f, pos, "redis-rs", "XADD")

    # ---------------------------------------------------------------- Redis streams
    def redis_stream_read(self, f, pos, a, meth):
        keys, group = [], None
        if any(_strlit(x) and _strlit(x).upper() == "STREAMS" for x in a):          # ioredis 'GROUP', g, c, ..., 'STREAMS', k, id
            i = next(i for i, x in enumerate(a) if _strlit(x) and _strlit(x).upper() == "STREAMS")
            rest = a[i + 1:]
            keys = rest[:len(rest) // 2] or rest[:1]
            if _strlit(a[0]) and _strlit(a[0]).upper() == "GROUP" and len(a) > 1:
                group = self.value(f, pos, a[1])[0]
        else:
            st = _kw(a, "streams")
            if meth in ("xreadgroup", "xReadGroup"):
                gexpr = _kw(a, "groupname") or (a[0] if a and "=" not in a[0] else None)
                group = self.value(f, pos, gexpr)[0] if gexpr else None
                st = st or next((x for x in a if x.strip().startswith(("{", "["))), None)
            else:
                st = st or (a[0] if a else None)
            st = (st or "").strip()
            if st.startswith("["):
                for o in _list(st):
                    k = _key(o, "key")
                    if k:
                        keys.append(k)
            elif st.startswith("{"):
                k = _key(st, "key")
                if k:
                    keys.append(k)
                else:
                    from .sockets import split_args
                    for kv in split_args(st[1:-1]):
                        mm = re.match(r"\s*(.+?)\s*:\s*", kv)
                        if mm:
                            keys.append(mm.group(1))
        for k in keys:
            v, c = self.value(f, pos, k)
            self.recv("redis-stream", v, c, f, pos, self.fn_at(f, pos), "redis", meth.upper(), group=group)

    # ---------------------------------------------------------------- AMQP
    def amqp_send(self, f, pos, ex, key, conf, lib, how):
        if ex is None:
            self.miss("amqp_send_unresolved", f"{f}:{self.s.line_of(f, pos)}")
            return
        if ex == "":
            self.send("amqp", None if key is None else f"queue:{key}", conf, f, pos, lib, how, exchange="")
        else:
            self.send("amqp", f"{ex}/{key if key is not None else '{key}'}", HEURISTIC if key is None else conf, f, pos, lib,
                      how, exchange=ex)

    def queue_id(self, f, pos, expr, depth=0):
        """Identity of the queue an expression names: `q:<name>` (named queue) or `anon:<file>:<fn>:<var>` (a
        server-named queue: assertQueue('') / queue_declare(exclusive=True) / `list($q,,) = queue_declare("")`)."""
        e = (expr or "").strip()
        if not e:
            return None
        e = re.sub(r"^&|\.as_str\(\)$", "", e)
        base = re.sub(r"(?:\.method)?\.queue$|\.name(?:\(\))?$|\.queueName$", "", e).lstrip("$")
        if base != e.lstrip("$") or re.fullmatch(r"\$?\w+", e):
            d = self.queue_decl(f, pos, base)
            if d is not None:
                return d
        if re.fullmatch(r"\$?\w+", e) and depth < 2:          # queue_name = result.method.queue
            src = self.s.text(f)
            _fn, lo, _hi = self.s.fn_bounds(f, pos)
            al = [m for m in re.finditer(rf"(?<![\w.$]){re.escape(e)}\s*=\s*([\w$]+(?:\.method)?\.(?:queue|queueName))\s*$", src[:pos], re.M)
                  if m.start() >= lo]
            if al:
                return self.queue_id(f, pos, al[-1].group(1), depth + 1)
        v, _c = self.value(f, pos, e)
        if v is None or _whole_ph(v):
            self.miss("amqp_queue_unresolved", f"{f}:{self.s.line_of(f, pos)} {e[:40]}")
            return None
        return f"q:{v}" if v else None

    def queue_decl(self, f, pos, var):
        src = self.s.text(f)
        fn, lo, hi = self.s.fn_bounds(f, pos)
        rx = re.compile(r"(?:(?:const|let|var|val)\s+(?:mut\s+)?(?:\{\s*queue\s*\}|" + re.escape(var) + r")|\$?" + re.escape(var) +
                        r"|(?:list\s*\(|\[)\s*\$" + re.escape(var) + r"\b[^=\n]*)\s*(?::\s*[\w<>?]+\s*)?=\s*(?:await\s+)?"
                        r"[\w$>.\s-]*?(?:assertQueue|queue_declare|declare_queue|queueDeclare)\s*\(")
        found = [m for m in rx.finditer(src, lo, max(pos, lo)) if m] or [m for m in rx.finditer(src)]
        if var == "queue":
            found += [m for m in re.finditer(r"\{\s*queue\s*\}\s*=\s*(?:await\s+)?[\w$.]*assertQueue\s*\(", src)]
        if not found:
            return None
        m = found[-1]
        a = _args(src, m.end() - 1)
        q = _kw(a, "queue") or _kw(a, "name") or (a[0] if a and "=" not in a[0] else None)
        v, _c = self.value(f, m.start(), q) if q else ("", RESOLVED)
        if v:
            return f"q:{v}"
        return f"anon:{f}:{self.s.line_of(f, m.start())}"

    def amqp_bind(self, f, pos, q, ex, key):
        qid = self.queue_id(f, pos, q)
        exv, _c1 = self.value(f, pos, ex)
        kv, c2 = self.value(f, pos, key)
        if qid is None or exv is None:
            self.miss("amqp_binding_unresolved", f"{f}:{self.s.line_of(f, pos)}")
            return
        self.bindings[qid].add((exv, kv if kv is not None else "#", f, pos, HEURISTIC if kv is None else c2))
        self.st["amqp_bindings"] += 1

    def amqp_bind_id(self, f, pos, qid, exv, key):
        kv, c2 = self.value(f, pos, key)
        self.bindings[qid].add((exv, kv if kv is not None else "#", f, pos, HEURISTIC if kv is None else c2))
        self.st["amqp_bindings"] += 1

    def finish_amqp(self):
        for qid, h, f, pos, lib, how in self.consumes:
            got = False
            for exv, kv, _bf, _bp, conf in sorted(self.bindings.get(qid, ())):
                t = self.exch_type.get(exv)
                key = "#" if t in ("fanout", "headers") else (kv if kv else "#" if t is None and kv == "" else kv)
                self.recv("amqp", f"{exv}/{key}", conf, f, pos, h, lib, how, queue=qid[2:] if qid.startswith("q:") else None,
                          exchange=exv, exchange_type=t)
                got = True
            if qid.startswith("q:"):
                self.recv("amqp", f"queue:{qid[2:]}", RESOLVED, f, pos, h, lib, how, queue=qid[2:])
            elif not got:
                self.miss("amqp_anonymous_queue_unbound", f"{f}:{self.s.line_of(f, pos)}")


def _kt_arr(e):
    """Kotlin / Java array or list argument text -> `[..]` (`["a", "b"]`, `arrayOf(..)`, `listOf(..)`, `{..}`)."""
    e = (e or "").strip()
    m = re.fullmatch(r"(?:arrayOf|listOf|setOf|mutableListOf)\s*\(([\s\S]*)\)|\{([\s\S]*)\}", e)
    if m:
        return "[" + (m.group(1) if m.group(1) is not None else m.group(2)) + "]"
    return e


def _rs_str(e):
    """A Rust argument without borrows and string conversions (`&key`, `"x".to_string()`, `String::from("x")`)."""
    e = (e or "").strip()
    e = re.sub(r"^&(?:mut\s+)?", "", e)
    m = re.fullmatch(r"(?:String::from|str::to_string)\s*\(([\s\S]*)\)", e)
    if m:
        e = m.group(1).strip()
    return re.sub(r"\.(?:to_string|to_owned|into|as_str|as_ref|clone)\(\)$", "", e)


def _bal(s):
    d = 0
    for c in s:
        d += c in "([{"
        d -= c in ")]}"
        if d < 0:
            return False
    return d == 0


def _whole_ph(v):
    """Nothing literal in the name but separators (`{x}`, `{base}/{topic}`)."""
    return v is not None and not re.sub(r"\{[^{}]*\}|[/.:_-]", "", v)


def _ident(e):
    ws = re.findall(r"[A-Za-z_]\w*", e or "")
    return ws[-1] if ws else "x"


def _regex_glob(rx):
    """A simple regex subscription as a glob (`^orders\\..*` -> `orders.*`), else None."""
    r = rx.strip("^$")
    r = r.replace(".*", "\0").replace("\\.", ".")
    if re.search(r"[\\()\[\]|+?{}^$]", r):
        return None
    return r.replace("\0", "*")


def apply(project, builder, sock=None) -> dict:
    """Broker / pub-sub endpoints of the project's messaging libraries (#35); empty without any."""
    return Scan(project, builder, sock).run()
