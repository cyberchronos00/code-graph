"""Job queues as protocol endpoints (#36): background jobs paired across services by task name and queue.

  endpoint:job:<framework>:<name>   one per task / job: RECEIVED_BY the function that runs it, SENDS_TO from the code
                                    that enqueues it by name or by reference (attrs.queue: the queue it is routed to)
  endpoint:queue:<framework>/<q>    one per named queue: SENDS_TO from enqueue sites that name the queue, QUEUE_ROUTES
                                    (non-propagating) to the tasks routed to it; attrs.consumers: the worker processes consuming it
                                    (Procfile / docker-compose / systemd / supervisor / scripts)

Part 1, Python:
  Celery      @app.task / @shared_task / @celery.task(name=, queue=), Task subclasses (Django plugin);
              x.delay / apply_async / s / si / signature / apply_async(queue=), send_task("name", queue=),
              task_routes / CELERY_TASK_ROUTES {"pattern": {"queue": q}}; workers `celery -A app worker -Q a,b`
              (default queue `celery`, or task_default_queue)
  RQ          Queue("name") variables, q.enqueue(func | "dotted.path" | f"..{x}"), enqueue_call(func=),
              django-rq get_queue("x").enqueue / django_rq.enqueue / @job("x") + f.delay(); workers `rq worker a b`,
              `manage.py rqworker a b` (default queue `default`)
  Dramatiq    @dramatiq.actor / @actor(actor_name=, queue_name=), f.send() / send_with_options(); workers
              `dramatiq mod -Q a` (default queue `default`)
The Django plugin's Celery `job` nodes keep their DISPATCHES / SCHEDULES; their endpoint twins carry the cross-repo
name (protocols/view.py shows the job node only).
"""
from __future__ import annotations

import os
import re
from collections import defaultdict

from . import presets
from .core.model import HEURISTIC, RESOLVED

TEST_FILE = re.compile(r"(?:^|/)(?:tests?|__tests__|spec)/|(?:^|/)test_[^/]*\.py$|_tests?\.py$|(?:^|/)conftest\.py$")
PROC_FILE = re.compile(r"(?:^|/)(?:Procfile[\w.-]*|(?:docker-)?compose[\w.-]*\.ya?ml|[\w.-]+\.service|supervisord?[\w.-]*\.conf|"
                       r"[\w.-]+\.conf|Dockerfile[\w.-]*|[\w.-]+\.sh|Makefile|fly\.toml|render\.ya?ml|app\.json|"
                       r"[\w.-]*(?:deploy|worker|k8s|helm)[\w.-]*\.ya?ml|entrypoint[\w.-]*|pyproject\.toml|tox\.ini|justfile)$")
RQ_FLAGS = {"-b", "--burst", "-s", "--with-scheduler", "-v", "--verbose", "-q", "--quiet", "--disable-job-desc-logging",
            "--disable-default-exception-handler", "--sentry-debug"}
DEFAULT_QUEUE = {"celery": "celery", "rq": "default", "dramatiq": "default", "laravel": "default", "messenger": "async"}
JS_EXT = (".ts", ".tsx", ".js", ".mjs", ".cjs", ".mts", ".cts")
JSSTR = r"""(?:'([^'\\\n]*)'|"([^"\\\n]*)"|`([^`$\\]*)`)"""

CELERY_DECO = re.compile(r"^[ \t]*@((?:[\w.]+\.)?(?:task|shared_task|periodic_task))\b", re.M)
RQ_JOB_DECO = re.compile(r"^[ \t]*@((?:django_rq\.)?job)\b", re.M)
ACTOR_DECO = re.compile(r"^[ \t]*@((?:dramatiq\.)?actor)\b", re.M)
STR = r"""(?:[rbuRBU]?(?:"([^"\\\n]*)"|'([^'\\\n]*)'))"""


def _lit(text: str) -> str | None:
    m = re.fullmatch(r"\s*[rbuRBU]?(?:\"([^\"\\\n]*)\"|'([^'\\\n]*)')\s*", text or "")
    return (m.group(1) if m.group(1) is not None else m.group(2)) if m else None


def _kw(args: str, name: str) -> str | None:
    """The text of keyword argument `name` (top level) of a call's argument text."""
    from .sockets import split_args
    for a in split_args(args or ""):
        k, eq, v = a.partition("=")
        if eq and k.strip() == name and not v.startswith("="):
            return v.strip()
    return None


def decorated(src: str, rx: re.Pattern):
    """(decorator name, argument text, function name, offset of the name) of each function decorated with rx's
    decorator (`@x` / `@x(...)`; arguments may nest and span lines), other decorators in between."""
    from .process_runs import _args_text
    for m in rx.finditer(src):
        i, args = m.end(), ""
        if src[i:i + 1] == "(":
            args = _args_text(src, i, 4000)
            i += len(args) + 2
        f = re.compile(r"[ \t]*(?:#[^\n]*)?\n(?:[ \t]*@[^\n]*\n|[ \t]*#[^\n]*\n)*[ \t]*(?:async\s+)?def\s+(\w+)").match(src, i)
        if f:
            yield m.group(1), args, f.group(1), f.start(1)


class Scan:
    def __init__(self, project, b, sock=None):
        from .sockets import Scan as SockScan
        self.root, self.b = project.root, b
        self.s = sock or SockScan(project, b)
        self.st = defaultdict(int)
        self.samples = defaultdict(list)
        self.tasks = {}                    # (fw, name) -> {nid, handler, file, line, queue, short, module}
        self.by_short = defaultdict(list)  # (fw, short name) -> [(fw, name)]
        self.queues_used = set()
        self.consumers = defaultdict(list)  # (fw, queue) -> [{process, at}]
        self.defaults = dict(DEFAULT_QUEUE)
        self.job_of_handler = {}
        self.dispatched = set()            # (src, line, job node) of the Django plugin's DISPATCHES / SCHEDULES
        self.done = set()
        self.fws = set()                   # frameworks with an enqueue site or a task
        self.uses = set()                  # frameworks the code imports
        for e in b.edges.values():
            if e.kind == "HANDLED_BY" and b.nodes.get(e.src) is not None and b.nodes[e.src].kind == "job":
                self.job_of_handler.setdefault(e.dst, e.src)
        for e in b.edges.values():
            if e.kind in ("DISPATCHES", "SCHEDULES") and e.dst in b.nodes and b.nodes[e.dst].kind == "job":
                self.dispatched.add((e.src, e.line, e.dst))
        self._consts = None
        self._enums = None

    def miss(self, key, text):
        self.st[key] += 1
        if len(self.samples[key]) < 6:
            self.samples[key].append(text[:100])

    # -------------------------------------------------------------- helpers
    def pyfiles(self):
        return [f for f in sorted(self.s.files) if f.endswith(".py")]

    def handler_at(self, file, line, name):
        for ln, _e, nid in self.s.spans.get(file, ()):
            if ln == line and (self.b.nodes[nid].name or "").split(".")[-1] == name:
                return nid
        for ln, _e, nid in self.s.spans.get(file, ()):
            if ln == line:
                return nid
        return None

    def qual(self, nid):
        return nid.split(":", 1)[1] if nid and nid.startswith(("function:", "method:")) else None

    def const(self, file, expr):
        """A queue / task name expression: a literal, or a NAME / settings.NAME / conf.NAME constant assigned a literal
        or os.environ.get("E", "literal") (heuristic, env) in the same file or a settings / config module."""
        if expr is None:
            return None, None
        v = _lit(expr)
        if v is not None:
            return v, RESOLVED
        m = re.fullmatch(r"\s*(?:[\w.]*\.)?([A-Z][A-Z0-9_]+)\s*", expr)
        if not m:
            return None, None
        name = m.group(1)
        if self._consts is None:
            self._consts = defaultdict(list)
            for f in self.pyfiles():
                if re.search(r"settings|config|conf|constants", f) and not TEST_FILE.search(f) and "/tests/" not in f:
                    for mm in re.finditer(r"^([A-Z][A-Z0-9_]+)\s*(?::[^=\n]+)?=[ \t]*([^\n]+)$", self.s.text(f), re.M):
                        self._consts[mm.group(1)].append((f, self._value(self.s.text(f), mm.start(2))))
        src = self.s.text(file)
        here = [(file, self._value(src, mm.start(1))) for mm in re.finditer(rf"^{name}\s*(?::[^=\n]+)?=[ \t]*([^\n]+)$", src, re.M)]
        cands = here or self._consts.get(name, [])
        if len({v for _f, v in cands}) != 1:
            return None, None                    # not found, or assigned differently in several settings modules
        v = cands[0][1]
        lit = _lit(v)
        if lit is not None:
            return lit, RESOLVED
        env = re.match(rf"\s*os\.(?:environ\.get|getenv)\(\s*{STR}\s*,\s*{STR}\s*\)", v)
        if env:
            return (env.group(3) if env.group(3) is not None else env.group(4)), HEURISTIC
        return None, None

    @staticmethod
    def _value(src, at):
        """The assigned expression from `at` (a call spanning lines: up to its closing parenthesis)."""
        from .process_runs import _args_text
        line = src[at:src.find("\n", at) if "\n" in src[at:] else len(src)]
        p = line.find("(")
        if p >= 0 and line.count("(") > line.count(")"):
            return re.sub(r"\s+", " ", line[:p] + "(" + _args_text(src, at + p, 800) + ")")
        return line.strip()

    # -------------------------------------------------------------- endpoints
    def job_ep(self, fw, name, handler, file, line, conf, how, queue=None):
        from .protocols import protocol_receive
        key = (fw, name)
        jid = self.job_of_handler.get(handler)
        protocol_receive(self.b, "job", f"{fw}:{name}", handler, file, line, conf, library=fw, how=how,
                         node_attrs={"framework": fw, "task": name, "queue": queue, "job_node": jid})
        nid = f"endpoint:job:{fw}:{name}"
        if jid and self.b.nodes[nid].entry_kind == "message_handler":
            self.b.nodes[nid].entry_kind = None          # the Django plugin's job node is the entry point
        self.tasks[key] = {"nid": nid, "handler": handler, "file": file, "line": line, "queue": queue, "job": jid,
                           "module": (self.qual(handler) or "").rsplit(".", 1)[0]}
        self.by_short[(fw, name.split(".")[-1])].append(key)
        self.by_short[(fw, (self.b.nodes[handler].name or "").split(".")[-1])].append(key)
        self.st[f"{fw}_tasks"] += 1
        if queue:
            self.queue_recv(fw, queue, handler, file, line, conf, f"{fw} task queue")

    def queue_recv(self, fw, queue, handler, file, line, conf, how):
        """QUEUE_ROUTES endpoint:queue:<fw>/<queue> -> the task routed to it (a receiver that does not propagate)."""
        from .protocols import _endpoint
        nid = _endpoint(self.b, "queue", f"{fw}/{queue}", {"framework": fw, "queue": queue})
        self.b.add_edge(nid, handler, "QUEUE_ROUTES", file=file, line=line, confidence=conf, library=fw, how=how)
        self.queues_used.add((fw, queue))

    def send(self, fw, name, file, pos, conf, how, queue=None, target=None, role="enqueue"):
        """SENDS_TO endpoint:job:<fw>:<name> (and the named queue) from the function around `pos`."""
        from .protocols import protocol_send
        from .tests_index import is_test_node
        if self.s.masked(file, pos):
            return
        fn, _lo, _hi = self.s.fn_bounds(file, pos)
        if fn is None:
            fn = self.module_of(file)
        if fn is None:
            self.miss("send_outside_function", f"{file}:{self.s.line_of(file, pos)}")
            return
        line = self.s.line_of(file, pos)
        self.fws.add(fw)
        if ("s", fw, name, fn, line) in self.done:
            return
        self.done.add(("s", fw, name, fn, line))
        n = self.b.nodes.get(fn)
        test = bool(n and is_test_node(n)) or bool(TEST_FILE.search(file))
        t = self.tasks.get((fw, name)) if target is None else target
        if not (t and t.get("job") and (fn, line, t["job"]) in self.dispatched):   # the plugin's DISPATCHES stays
            protocol_send(self.b, "job", f"{fw}:{name}", fn, file, line, conf, test=test, role=role, library=fw, how=how,
                          queue=queue, node_attrs={"framework": fw, "task": name})
            self.st[f"{fw}_sends"] += 1
        if queue:
            protocol_send(self.b, "queue", f"{fw}/{queue}", fn, file, line, conf, test=test, role=role, library=fw,
                          how=how, task=name, node_attrs={"framework": fw, "queue": queue})
            self.queues_used.add((fw, queue))
            self.st[f"{fw}_queue_sends"] += 1

    def module_of(self, file):
        for nid, n in self.b.nodes.items():
            if n.kind == "module" and n.file == file:
                return nid
        return None

    def resolve_ref(self, fw, file, ident):
        """The task a Python name refers to (`charge` / `tasks.charge`): defined in `file`, imported, or unique."""
        short = ident.split(".")[-1]
        keys = list(dict.fromkeys(self.by_short.get((fw, short)) or []))
        if not keys:
            return None, None
        here = [k for k in keys if self.tasks[k]["file"] == file]
        if here:
            return here[0], RESOLVED
        src = self.s.text(file)
        m = re.search(rf"^\s*from\s+([.\w]+)\s+import\s+(?:\([^)]*\b{re.escape(short)}\b|[^\n]*\b{re.escape(short)}\b)", src, re.M)
        if m:
            mod = m.group(1).lstrip(".")
            hit = [k for k in keys if self.tasks[k]["module"].endswith(mod)]
            if len(hit) == 1:
                return hit[0], RESOLVED
        if "." in ident:
            mod = ident.rsplit(".", 1)[0]
            hit = [k for k in keys if self.tasks[k]["module"].split(".")[-1] == mod.split(".")[-1]]
            if len(hit) == 1:
                return hit[0], RESOLVED
        return (keys[0], HEURISTIC) if len(keys) == 1 else (None, None)

    # -------------------------------------------------------------- Celery
    def celery(self):
        uses = any(re.search(r"^\s*(?:from\s+celery\b|import\s+celery\b)", self.s.text(f), re.M) for f in self.pyfiles())
        if not uses:
            return
        self.uses.add("celery")
        for f in self.pyfiles():
            src = self.s.text(f)
            for m in re.finditer(r"\b(?:task_default_queue|CELERY_TASK_DEFAULT_QUEUE|CELERY_DEFAULT_QUEUE)\s*=\s*([^\n,)]+)", src):
                v, _c = self.const(f, m.group(1))
                if v:
                    self.defaults["celery"] = v
            if ".task" not in src and "shared_task" not in src:
                continue
            for dn, args, fname, at in decorated(src, CELERY_DECO):
                if dn == "task" and "celery" not in src or self.s.masked(f, at):
                    continue
                if dn.split(".")[-1] == "task" and dn != "task" and dn.split(".")[0] in ("pytest", "invoke", "nox", "huey"):
                    continue
                line = self.s.line_of(f, at)
                h = self.handler_at(f, line, fname)
                if not h:
                    self.miss("celery_task_without_function", f"{f}:{line}")
                    continue
                name, _c = self.const(f, _kw(args, "name"))
                name = name or self.qual(h)
                q, _qc = self.const(f, _kw(args, "queue"))
                self.job_ep("celery", name, h, f, line, RESOLVED, f"@{dn}", queue=q)
        # Task subclasses of the Django plugin (job nodes without a decorator)
        for nid, n in list(self.b.nodes.items()):
            if n.kind == "job" and n.lang == "python" and not (n.attrs or {}).get("unresolved_task") and \
                    (n.attrs or {}).get("decorator") is None and (n.attrs or {}).get("framework") is None:
                h = next((e.dst for e in self.b.edges.values() if e.src == nid and e.kind == "HANDLED_BY"), None)
                if h and not any(t["handler"] == h for t in self.tasks.values()):
                    self.job_ep("celery", n.fqn or n.name, h, n.file, n.line, RESOLVED, "Task subclass")
        self.celery_routes()
        for f in self.pyfiles():
            src = self.s.text(f)
            if not re.search(r"\.(?:delay|apply_async|s|si|signature|send_task|delay_on_commit)\(", src):
                continue
            for m in re.finditer(r"\b([A-Za-z_][\w.]*)\.(delay|apply_async|s|si|signature|delay_on_commit)\s*\(", src):
                ident = m.group(1)
                if ident.split(".")[-1] in ("self", "cls", "app", "celery", "group", "chain", "chord"):
                    continue
                key, conf = self.resolve_ref("celery", f, ident)
                if not key:
                    continue
                from .sockets import _args_text
                args = _args_text(src, m.end() - 1, 2000) if m.group(2) == "apply_async" else ""
                q, _qc = self.const(f, _kw(args, "queue")) if args else (None, None)
                self.send("celery", key[1], f, m.start(), conf, f".{m.group(2)}()", queue=q or self.tasks[key]["queue"])
            for m in re.finditer(r"\.(send_task|signature)\s*\(\s*" + STR, src):
                if m.group(1) == "signature" and not re.search(r"\b(?:app|celery|current_app)\s*\.\s*$", src[max(0, m.start() - 20):m.start() + 1]):
                    continue
                from .sockets import _args_text
                name = m.group(2) if m.group(2) is not None else m.group(3)
                args = _args_text(src, src.index("(", m.start()), 2000)
                q, _qc = self.const(f, _kw(args, "queue"))
                q = q or self.route_for(name)
                self.send("celery", name, f, m.start(), RESOLVED, m.group(1), queue=q)

    def celery_routes(self):
        """task_routes = {"pattern": {"queue": q}} / CELERY_TASK_ROUTES: the queue of each matching task."""
        self.routes = []
        for f in self.pyfiles():
            src = self.s.text(f)
            for m in re.finditer(r"\b(?:task_routes|CELERY_TASK_ROUTES|CELERY_ROUTES)\s*=\s*\{", src):
                from .rpc import _block
                lo, hi = _block(src, m.end() - 1)
                for r in re.finditer(STR + r"\s*:\s*\{[^{}]*?['\"]queue['\"]\s*:\s*([^,}\n]+)", src[lo:hi]):
                    pat = r.group(1) if r.group(1) is not None else r.group(2)
                    q, _c = self.const(f, r.group(3))
                    if q:
                        self.routes.append((pat, q, f, self.s.line_of(f, lo + r.start())))
        import fnmatch
        for (fw, name), t in list(self.tasks.items()):
            if fw != "celery" or t["queue"]:
                continue
            for pat, q, f, line in self.routes:
                if fnmatch.fnmatchcase(name, pat):
                    t["queue"] = q
                    self.b.nodes[t["nid"]].attrs["queue"] = q
                    self.queue_recv("celery", q, t["handler"], f, line, RESOLVED, "task_routes")
                    break

    def route_for(self, name):
        import fnmatch
        for pat, q, _f, _l in getattr(self, "routes", ()):
            if fnmatch.fnmatchcase(name, pat):
                return q
        return None

    # -------------------------------------------------------------- RQ / django-rq
    def rq(self):
        if not any(re.search(r"^\s*(?:from\s+(?:rq|django_rq)\b|import\s+(?:rq|django_rq)\b)", self.s.text(f), re.M) for f in self.pyfiles()):
            return
        self.uses.add("rq")
        for f in self.pyfiles():
            src = self.s.text(f)
            for _dn, args, fname, at in decorated(src, RQ_JOB_DECO):
                if self.s.masked(f, at):
                    continue
                line = self.s.line_of(f, at)
                h = self.handler_at(f, line, fname)
                if h:
                    from .sockets import split_args
                    a0 = (split_args(args) or [""])[0]
                    q, _c = self.const(f, a0 if "=" not in a0 else None)
                    self.job_ep("rq", self.qual(h), h, f, line, RESOLVED, "@job", queue=q)
        qvars = {}                       # (file, var) -> queue
        for f in self.pyfiles():
            src = self.s.text(f)
            for m in re.finditer(r"([\w.]+)\s*=\s*(?:rq\.)?Queue\s*\(([^)\n]*)\)", src):
                from .sockets import split_args
                a = split_args(m.group(2))
                q = _lit(a[0]) if a and "=" not in a[0] else _lit(_kw(m.group(2), "name") or "")
                qvars[m.group(1).split(".")[-1]] = q or "default"
        funcs = {}
        for nid, n in self.b.nodes.items():
            if n.kind == "function" and n.file and n.file.endswith(".py"):
                funcs.setdefault(self.qual(nid), nid)
        for f in self.pyfiles():
            src = self.s.text(f)
            if "enqueue" not in src and ".delay(" not in src:
                continue
            for m in re.finditer(r"(?:\b([\w.]+)|get_queue\(\s*" + STR + r"\s*\)|get_queue\(\s*\))\.(enqueue|enqueue_call|enqueue_in|enqueue_at)\s*\(", src):
                recv = m.group(1) or ""
                if m.group(1) is not None:
                    last = recv.split(".")[-1]
                    if last == "django_rq":
                        q = "default"
                    elif last in qvars:
                        q = qvars[last]
                    elif last in ("task_queue", "queue", "q") or "queue" in last.lower():
                        q = next(iter(set(qvars.values()))) if len(set(qvars.values())) == 1 else None
                    else:
                        continue
                else:
                    q = (m.group(2) if m.group(2) is not None else m.group(3)) or "default"
                from .sockets import _args_text, split_args
                args = _args_text(src, m.end() - 1, 2000)
                a = split_args(args)
                if m.group(4) in ("enqueue_in", "enqueue_at"):
                    a = a[1:]
                target = _kw(args, "func") if m.group(4) == "enqueue_call" else (a[0] if a else None)
                if not target:
                    continue
                self._rq_target(f, m.start(), target.strip(), q, funcs)
            for m in re.finditer(r"\b([A-Za-z_][\w.]*)\.delay\s*\(", src):
                key, conf = self.resolve_ref("rq", f, m.group(1))
                if key:
                    self.send("rq", key[1], f, m.start(), conf, "@job .delay()", queue=self.tasks[key]["queue"])

    def _rq_target(self, f, pos, target, q, funcs):
        lit = _lit(target)
        fs = re.fullmatch(r"[fF](?:\"([^\"\n]*)\"|'([^'\n]*)')", target)
        if lit is not None:
            name, conf = lit, RESOLVED
        elif fs:
            self._rq_template_module(fs.group(1) or fs.group(2), q)
            name, conf = re.sub(r"\{\s*([\w.]*)[^{}]*\}", lambda x: "{" + (x.group(1).split(".")[-1] or "x") + "}",
                                fs.group(1) or fs.group(2)), HEURISTIC
        elif re.fullmatch(r"[A-Za-z_][\w.]*", target):
            h, conf = self._py_func(f, target, funcs)
            if not h:
                self.miss("rq_target_unresolved", f"{f}:{self.s.line_of(f, pos)} {target}")
                return
            name = self.qual(h)
            if ("rq", name) not in self.tasks:
                self.job_ep("rq", name, h, self.b.nodes[h].file, self.b.nodes[h].line, RESOLVED, "enqueued function")
        else:
            return
        if ("rq", name) not in self.tasks and lit is not None and name in funcs:
            h = funcs[name]
            self.job_ep("rq", name, h, self.b.nodes[h].file, self.b.nodes[h].line, RESOLVED, "enqueued by path")
        self.send("rq", name, f, pos, conf, "enqueue", queue=q)
        if q:
            for t in [self.tasks.get(("rq", name))]:
                if t and not t["queue"]:
                    t["queue"] = q
                    self.b.nodes[t["nid"]].attrs["queue"] = q
                    self.queue_recv("rq", q, t["handler"], t["file"], t["line"], RESOLVED, "enqueued on")

    def _rq_template_module(self, text, queue):
        """f"app.tasks.{name}" / f"app.tasks.send_{kind}": every top-level function of module app.tasks matching the
        template is a job RQ can run by that name (heuristic; matched to the template)."""
        from .protocols import _path_template
        path = re.sub(r"\{\s*([\w.]*)[^{}]*\}", lambda x: "{" + (x.group(1).split(".")[-1] or "x") + "}", text)
        mod, rx = _path_template(path)
        if not mod or ("rq-mod", path) in self.done:
            return
        self.done.add(("rq-mod", path))
        for nid, n in list(self.b.nodes.items()):
            q = self.qual(nid)
            if n.kind == "function" and q and q.rsplit(".", 1)[0] == mod and rx.fullmatch(q) and not n.name.startswith("_") \
                    and ("rq", q) not in self.tasks:
                self.job_ep("rq", q, nid, n.file, n.line, HEURISTIC, "function of the enqueued module", queue=queue)

    def _py_func(self, file, ident, funcs):
        short = ident.split(".")[-1]
        here = [nid for _l, _e, nid in self.s.spans.get(file, ()) if (self.b.nodes[nid].name or "") == short and nid.startswith("function:")]
        if len(here) == 1:
            return here[0], RESOLVED
        src = self.s.text(file)
        m = re.search(rf"^\s*from\s+([.\w]+)\s+import\s+(?:\([^)]*\b{re.escape(short)}\b|[^\n]*\b{re.escape(short)}\b)", src, re.M)
        if m:
            mod = m.group(1).lstrip(".")
            hit = [nid for q, nid in funcs.items() if q and q.endswith(f"{mod}.{short}")]
            if len(hit) == 1:
                return hit[0], RESOLVED
        hit = [nid for q, nid in funcs.items() if q and q.split(".")[-1] == short]
        return (hit[0], HEURISTIC) if len(hit) == 1 else (None, None)

    # -------------------------------------------------------------- Dramatiq
    def dramatiq(self):
        if not any(re.search(r"^\s*(?:from\s+dramatiq\b|import\s+dramatiq\b)", self.s.text(f), re.M) for f in self.pyfiles()):
            return
        self.uses.add("dramatiq")
        for f in self.pyfiles():
            src = self.s.text(f)
            if "actor" not in src:
                continue
            if not re.search(r"\bdramatiq\b|import[^\n]*\bactor\b", src):
                continue
            for dn, args, fname, at in decorated(src, ACTOR_DECO):
                if self.s.masked(f, at):
                    continue
                line = self.s.line_of(f, at)
                h = self.handler_at(f, line, fname)
                if not h:
                    continue
                name = _lit(_kw(args, "actor_name") or "") or fname
                q, _c = self.const(f, _kw(args, "queue_name"))
                self.job_ep("dramatiq", name, h, f, line, RESOLVED, f"@{dn}", queue=q)
        for f in self.pyfiles():
            src = self.s.text(f)
            if ".send" not in src and "actor" not in src:
                continue
            for m in re.finditer(r"\b([A-Za-z_][\w.]*)\.(send|send_with_options)\s*\(", src):
                key, conf = self.resolve_ref("dramatiq", f, m.group(1))
                if key:
                    from .sockets import _args_text
                    q, _c = self.const(f, _kw(_args_text(src, m.end() - 1, 1500), "queue_name")) if m.group(2) == "send_with_options" else (None, None)
                    self.send("dramatiq", key[1], f, m.start(), conf, f".{m.group(2)}()",
                              queue=q or self.tasks[key]["queue"])
            for m in re.finditer(r"\bactor\s*=\s*([A-Za-z_][\w.]*)\s*[,)\n]", src):
                key, _conf = self.resolve_ref("dramatiq", f, m.group(1))
                if key:                 # ScheduleSpec(actor=x, crontab=..) and similar registrations: x runs later
                    self.send("dramatiq", key[1], f, m.start(), HEURISTIC, "actor=", queue=self.tasks[key]["queue"],
                              role="schedule")

    # -------------------------------------------------------------- Bull / BullMQ (outside Nest)
    def js_name(self, file, expr):
        """A queue / job name: a string literal, or `Enum.Member` / `CONST.member` of a TS enum or object constant."""
        expr = (expr or "").strip()
        m = re.fullmatch(JSSTR, expr)
        if m:
            return next(g for g in m.groups() if g is not None), RESOLVED
        m = re.fullmatch(r"([A-Z]\w*)\.(\w+)", expr)
        if not m:
            return None, None
        if self._enums is None:
            self._enums = defaultdict(dict)
            for f in self.s.files:
                if not f.endswith(JS_EXT):
                    continue
                t = self.s.text(f)
                if "enum " not in t and " as const" not in t:
                    continue
                for e in re.finditer(r"\benum\s+(\w+)\s*\{([^{}]*)\}|\bconst\s+(\w+)\s*=\s*\{([^{}]*)\}\s*as\s+const", t):
                    name, body = (e.group(1), e.group(2)) if e.group(1) else (e.group(3), e.group(4))
                    for mm in re.finditer(r"(\w+)\s*[=:]\s*" + JSSTR, body):
                        self._enums[name].setdefault(mm.group(1), next(g for g in mm.groups()[1:] if g is not None))
        v = self._enums.get(m.group(1), {}).get(m.group(2))
        return (v, RESOLVED) if v is not None else (None, None)

    def bull(self):
        files = [f for f in sorted(self.s.files) if f.endswith(JS_EXT) and not f.endswith(".d.ts")]
        libs = [f for f in files if re.search(r"""(?:from\s+|require\(\s*)['"](?:bull|bullmq)['"]""", self.s.text(f))]
        if not libs:
            return
        self.uses.add("bull")
        from .process_runs import _args_text
        from .sockets import split_args
        binds = defaultdict(set)              # variable / getter function short name -> queue names
        factories = set()                     # functions turning their first parameter into `new Queue(param)`
        for f in libs:
            src = self.s.text(f)
            for m in re.finditer(r"\bnew\s+(?:Bull\.)?(Queue|Bull|Worker|QueueEvents)\b\s*(?:<[^()]*?>)?\s*\(", src):
                if self.s.masked(f, m.start()):
                    continue
                a = split_args(_args_text(src, m.end() - 1, 3000))
                if not a:
                    continue
                q, conf = self.js_name(f, a[0])
                fn, _lo, _hi = self.s.fn_bounds(f, m.start())
                if q is None:
                    if fn and re.fullmatch(r"\w+", a[0].strip()) and a[0].strip() in [p for p, _d in self.s.params(f, fn)]:
                        factories.add((self.b.nodes[fn].name or "").split(".")[-1])
                    else:
                        self.miss("bull_queue_name_unresolved", f"{f}:{self.s.line_of(f, m.start())} {a[0][:40]}")
                    continue
                if m.group(1) == "Worker":
                    h = self.s.handler(a[1], f) if len(a) > 1 else None
                    self.bull_consume(f, m.start(), q, None, h, conf, "new Worker")
                    continue
                if m.group(1) == "QueueEvents":
                    continue
                v = re.search(r"(?:(?:const|let|var)\s+|this\.|\b)(\w+)\s*(?::[^=;\n]+)?=\s*$", src[max(0, m.start() - 120):m.start()])
                if v:
                    binds[v.group(1)].add(q)
                if fn:
                    binds[(self.b.nodes[fn].name or "").split(".")[-1]].add(q)
        if factories:
            rx = re.compile(r"\b(" + "|".join(map(re.escape, sorted(factories))) + r")\s*(?:<[^()]*?>)?\s*\(")
            for f in files:
                src = self.s.text(f)
                for m in rx.finditer(src):
                    if self.s.masked(f, m.start()) or re.search(r"(?:function\s+|\bdef\s+)$", src[max(0, m.start() - 10):m.start()]):
                        continue
                    a = split_args(_args_text(src, m.end() - 1, 2000))
                    q, _c = self.js_name(f, a[0]) if a else (None, None)
                    if q is None:
                        continue
                    v = re.search(r"(?:(?:const|let|var)\s+|this\.|\b)(\w+)\s*(?::[^=;\n]+)?=\s*$", src[max(0, m.start() - 120):m.start()])
                    if v:
                        binds[v.group(1)].add(q)
                    fn, _lo, _hi = self.s.fn_bounds(f, m.start())
                    if fn:
                        binds[(self.b.nodes[fn].name or "").split(".")[-1]].add(q)
                    self.st["bull_factory_queues"] += 1
        binds = {k: v for k, v in binds.items() if len(v) == 1}
        if not binds:
            return
        rx = re.compile(r"(?:\bthis\.|\b)(" + "|".join(map(re.escape, sorted(binds))) + r")(\s*\(\s*\))?\s*\.\s*(add|addBulk|process)\s*\(")
        for f in files:
            src = self.s.text(f)
            if ".add" not in src and ".process" not in src:
                continue
            for m in rx.finditer(src):
                if self.s.masked(f, m.start()):
                    continue
                q = next(iter(binds[m.group(1)]))
                a = split_args(_args_text(src, m.end() - 1, 3000))
                if m.group(3) == "process":
                    name, conf = self.js_name(f, a[0]) if a else (None, None)
                    h = self.s.handler(a[-1], f) if a else None
                    self.bull_consume(f, m.start(), q, name, h, RESOLVED if name or not a else conf, ".process()")
                elif m.group(3) == "add":
                    name, _c = self.js_name(f, a[0]) if len(a) >= 2 else (None, None)
                    self.bull_send(f, m.start(), q, name, ".add()")
                else:
                    self.bull_send(f, m.start(), q, None, ".addBulk()")

    def bull_consume(self, file, pos, q, name, handler, conf, how):
        fn = handler
        if fn is None:
            fn, _lo, _hi = self.s.fn_bounds(file, pos)
        if fn is None:
            fn = self.module_of(file)
        if fn is None:
            return
        line = self.s.line_of(file, pos)
        self.st["bull_consumers"] += 1
        # a named processor takes `queue.add(name, ..)` of that name; a queue-wide one (`new Worker(q, fn)`,
        # `queue.process(fn)`) any name: the template `<queue>:{name}` (the more specific named one wins a match)
        self.job_ep("bull", f"{q}:{name}" if name else f"{q}:{{name}}", fn, file, line, conf or RESOLVED, how, queue=q)

    def bull_send(self, file, pos, q, name, how):
        if name:
            self.send("bull", f"{q}:{name}", file, pos, RESOLVED, how, queue=q)
        else:
            self.send_queue("bull", q, file, pos, RESOLVED, how)

    def send_queue(self, fw, q, file, pos, conf, how, task=None):
        """SENDS_TO endpoint:queue:<fw>/<q> only (a job without its own name: Bull `queue.add(data)`)."""
        from .protocols import protocol_send
        from .tests_index import is_test_node
        if self.s.masked(file, pos):
            return
        fn, _lo, _hi = self.s.fn_bounds(file, pos)
        fn = fn or self.module_of(file)
        if fn is None:
            return
        line = self.s.line_of(file, pos)
        if ("q", fw, q, fn, line) in self.done:
            return
        self.done.add(("q", fw, q, fn, line))
        self.fws.add(fw)
        n = self.b.nodes.get(fn)
        test = bool(n and is_test_node(n)) or bool(TEST_FILE.search(file)) or bool(re.search(r"\.(?:test|spec)\.[jt]sx?$", file))
        protocol_send(self.b, "queue", f"{fw}/{q}", fn, file, line, conf, test=test, role="enqueue", library=fw, how=how,
                      task=task, node_attrs={"framework": fw, "queue": q})
        self.queues_used.add((fw, q))
        self.st[f"{fw}_queue_sends"] += 1

    # -------------------------------------------------------------- twins of the plugins' job nodes
    def twins(self):
        """Nest Bull / BullMQ processors (`job:<queue>[:<name>]`) and Laravel jobs (`job:<Class>`) keep their nodes,
        DISPATCHES and entries; endpoint twins carry the names `cg link` pairs across repos."""
        for nid, n in list(self.b.nodes.items()):
            if n.kind != "job" or n.lang not in ("ts", "php"):
                continue
            a = n.attrs or {}
            h = next((e.dst for e in self.b.edges.values() if e.src == nid and e.kind == "HANDLED_BY"), None)
            if not h:
                continue
            if n.lang == "ts" and a.get("queue"):
                q = a["queue"]
                self.job_ep("bull", f"{q}:{a['job']}" if a.get("job") else f"{q}:{{name}}", h, n.file, n.line, RESOLVED,
                            f"{'@Process' if a.get('job') else 'WorkerHost'} (nest {a.get('framework')})", queue=q)
                self.uses.add("bull")
                self.st["nest_job_twins"] += 1
            elif n.lang == "php":
                if "ShouldQueue" not in self.s.text(n.file) if n.file else True:
                    self.st["laravel_sync_jobs"] += 1   # an App\Jobs class without ShouldQueue runs in the dispatching process
                    continue
                self.job_ep("laravel", n.fqn or nid.split(":", 1)[1], h, n.file, n.line, RESOLVED, "ShouldQueue job")
                self.uses.add("laravel")
                self.st["laravel_job_twins"] += 1

    # -------------------------------------------------------------- Laravel queues
    def laravel(self):
        if not any(k[0] == "laravel" for k in self.tasks):
            return
        php = [f for f in sorted(self.s.files) if f.endswith(".php")]
        self.job_tasks = {}                      # the plugin dispatches to the job node or (via=job) its handle()
        for (fw, _n), t in self.tasks.items():
            if fw == "laravel" and t["job"]:
                self.job_tasks[t["job"]] = t
                self.job_tasks[t["handler"]] = t
        for (fw, name), t in self.tasks.items():
            if fw != "laravel":
                continue
            src = self.s.text(t["file"])
            m = re.search(r"(?:public|protected|private)\s+(?:\??string\s+)?\$queue\s*=\s*['\"]([\w.:-]+)['\"]|"
                          r"\$this->(?:onQueue\(\s*|queue\s*=\s*)['\"]([\w.:-]+)['\"]|#\[\s*(?:\\?Illuminate\\Queue\\Attributes\\)?OnQueue\(\s*['\"]([\w.:-]+)", src)
            if m:
                q = m.group(1) or m.group(2) or m.group(3)
                t["queue"] = q
                self.b.nodes[t["nid"]].attrs["queue"] = q
                self.queue_recv("laravel", q, t["handler"], t["file"], self.s.line_of(t["file"], m.start()), RESOLVED, "job queue")
        for e in list(self.b.edges.values()):     # the plugin's dispatches of a job with its own queue: to that queue
            if e.kind == "DISPATCHES" and e.dst in self.job_tasks and e.src in self.b.nodes and e.line:
                t = self.job_tasks[e.dst]
                f = self.b.nodes[e.src].file
                if t["queue"] and f and f.endswith(".php"):
                    src = self.s.text(f)
                    at = self.s.off(f, e.line)
                    end = src.find(";", at)
                    if "onQueue" in src[at:end if end > 0 else at + 400]:
                        continue                      # ->onQueue('x') overrides the job's own queue (below)
                    self.send_queue("laravel", t["queue"], f, self.s.off(f, e.line), RESOLVED, "job $queue",
                                    task=self.b.nodes[t["nid"]].attrs.get("task"))
        short = defaultdict(list)
        for (fw, name), t in self.tasks.items():
            if fw == "laravel":
                short[name.split("\\")[-1]].append(t)
        disp = defaultdict(list)                 # (file, line) -> job nodes the plugin dispatches there
        for e in self.b.edges.values():
            if e.kind == "DISPATCHES" and e.dst in self.job_tasks:
                disp[(self.b.nodes[e.src].file if e.src in self.b.nodes else None, e.line)].append(self.job_tasks[e.dst]["job"])
        for f in php:
            src = self.s.text(f)
            if "onQueue" not in src:
                continue
            for m in re.finditer(r"->\s*onQueue\(\s*['\"]([\w.:-]+)['\"]", src):
                if self.s.masked(f, m.start()):
                    continue
                stmt_lo = max(src.rfind(";", 0, m.start()), src.rfind("{", 0, m.start()), src.rfind("}", 0, m.start())) + 1
                stmt = src[stmt_lo:m.start()]
                found = []
                for c in re.finditer(r"\b([A-Z]\w*|self|static)::dispatch(?:Sync|AfterResponse)?\s*\(|\bnew\s+\\?(?:[\w\\]+\\)?([A-Z]\w*)\s*\(", stmt):
                    cls = c.group(1) or c.group(2)
                    if cls in ("self", "static"):
                        k = re.search(r"^\s*(?:final\s+|abstract\s+)?class\s+(\w+)", src, re.M)
                        cls = k.group(1) if k else cls
                    if cls in short and cls not in [x[0] for x in found]:
                        found.append((cls, stmt_lo + c.start()))   # a job, or each job of a Bus::batch / chain
                if not found:
                    self.miss("laravel_onqueue_unresolved", f"{f}:{self.s.line_of(f, m.start())}")
                    continue
                q = m.group(1)
                for cls, at in found:
                    cands = short[cls]
                    line = self.s.line_of(f, at)
                    hit = [t for t in cands if any(j == t["job"] for ln in range(line, self.s.line_of(f, m.start()) + 1)
                                                   for j in disp.get((f, ln), ()))]
                    t = hit[0] if hit else (cands[0] if len(cands) == 1 else None)
                    self.send_queue("laravel", q, f, at, RESOLVED if t else HEURISTIC, "->onQueue()",
                                    task=t and self.b.nodes[t["nid"]].attrs.get("task"))
                    if t:
                        self.queue_recv("laravel", q, t["handler"], f, line, HEURISTIC, "dispatched onQueue")
        self.horizon()

    def horizon(self):
        """config/horizon.php supervisors: `'supervisor-x' => [... 'queue' => ['a', 'b'] ...]` (defaults and
        environments): the queues `php artisan horizon` consumes."""
        f = "config/horizon.php"
        if not (self.root / f).is_file():
            return
        t = (self.root / f).read_text(encoding="utf-8", errors="replace")
        for m in re.finditer(r"['\"]([\w.-]+)['\"]\s*=>\s*\[(?:[^\[\]]|\[[^\[\]]*\])*?['\"]queue['\"]\s*=>\s*(\[[^\]]*\]|['\"][^'\"]+['\"])", t):
            qs = re.findall(r"['\"]([\w.:-]+)['\"]", m.group(2))
            line = t.count("\n", 0, m.start()) + 1
            for q in qs:
                self.consumers[("laravel", q)].append({"process": f"horizon:{m.group(1)}", "at": f"{f}:{line}"})
            self.st["horizon_supervisors"] += 1

    # -------------------------------------------------------------- Symfony Messenger
    @staticmethod
    def php_fqcn(src, short):
        """FQCN of a class name in a PHP file: its `use` statements (aliases too), else the file's namespace."""
        short = short.lstrip("\\")
        if "\\" in short:
            return short
        m = re.search(rf"^\s*use\s+\\?([\w\\]+\\{re.escape(short)})\s*;|^\s*use\s+\\?([\w\\]+)\s+as\s+{re.escape(short)}\s*;", src, re.M)
        if m:
            return m.group(1) or m.group(2)
        ns = re.search(r"^\s*namespace\s+([\w\\]+)\s*;", src, re.M)
        return f"{ns.group(1)}\\{short}" if ns else short

    def messenger(self):
        php = [f for f in sorted(self.s.files) if f.endswith(".php")]
        hfiles = [f for f in php if re.search(r"AsMessageHandler|MessageHandlerInterface|MessageSubscriberInterface", self.s.text(f))]
        routing = self.messenger_routing()
        if not hfiles and not routing:
            return
        self.uses.add("messenger")
        for f in hfiles:
            src = self.s.text(f)
            cls = re.search(r"^\s*(?:final\s+|abstract\s+|readonly\s+)*class\s+(\w+)([^{]*)", src, re.M)
            if not cls:
                continue
            cfq = self.php_fqcn(src, cls.group(1))
            on_class = re.search(r"#\[\s*(?:\\?[\w\\]*\\)?AsMessageHandler\b(\((?:[^()]|\([^()]*\))*\))?\s*\]\s*(?:#\[[^\]]*\]\s*)*"
                                 r"(?:final\s+|readonly\s+)*class\b", src)
            methods = []
            if on_class or re.search(r"\bimplements\b[^{]*\bMessageHandlerInterface\b", cls.group(2)):
                meth = _lit(_kw((on_class.group(1) or "()")[1:-1], "method") or "") if on_class else None
                methods.append((meth or "__invoke", (on_class.group(1) or "") if on_class else ""))
            for m in re.finditer(r"#\[\s*(?:\\?[\w\\]*\\)?AsMessageHandler\b(\((?:[^()]|\([^()]*\))*\))?\s*\]\s*(?:#\[[^\]]*\]\s*)*"
                                 r"(?:public\s+)?function\s+(\w+)", src):
                methods.append((m.group(2), m.group(1) or ""))
            for meth, args in methods:
                h = f"method:{cfq}::{meth}"
                if h not in self.b.nodes:
                    self.miss("messenger_handler_without_method", f"{f} {meth}")
                    continue
                handles = re.search(r"handles\s*:\s*\\?([\w\\]+)::class", args)
                if handles:
                    msg = self.php_fqcn(src, handles.group(1))
                else:
                    sig = re.search(rf"function\s+{re.escape(meth)}\s*\(\s*(?:[\w\\]+\s*\|\s*)?\\?([\w\\]+)\s+\$", src)
                    if not sig:
                        self.miss("messenger_message_type_unknown", f"{f} {meth}")
                        continue
                    msg = self.php_fqcn(src, sig.group(1))
                tr = re.search(r"fromTransport\s*:\s*['\"]([\w.-]+)['\"]", args)
                n = self.b.nodes[h]
                self.job_ep("messenger", msg, h, n.file, n.line, RESOLVED, "#[AsMessageHandler]",
                            queue=tr.group(1) if tr else None)
        for (msg, tr, f, line) in routing:
            t = self.tasks.get(("messenger", msg))
            if t:
                if not t["queue"]:
                    t["queue"] = tr
                    self.b.nodes[t["nid"]].attrs["queue"] = tr
                if t["queue"] == tr:
                    self.queue_recv("messenger", tr, t["handler"], f, line, RESOLVED, "messenger routing")
        routes = defaultdict(list)
        for msg, tr, _f, _l in routing:
            routes[msg].append(tr)
        for f in php:
            src = self.s.text(f)
            if "dispatch" not in src:
                continue
            for m in re.finditer(r"(\$[\w>-]*(?:bus|Bus)\w*)\s*->\s*dispatch\s*\(\s*new\s+\\?([\w\\]+)\s*\(|"
                                 r"\$this->dispatchMessage\s*\(\s*new\s+\\?([\w\\]+)\s*\(", src):
                if self.s.masked(f, m.start()):
                    continue
                msg = self.php_fqcn(src, m.group(2) or m.group(3))
                self.send("messenger", msg, f, m.start(), RESOLVED, "bus->dispatch()", queue=None)
                for tr in routes.get(msg) or []:
                    self.send_queue("messenger", tr, f, m.start(), RESOLVED, "messenger routing", task=msg)

    def messenger_routing(self):
        """[(message FQCN, transport, file, line)] of `framework.messenger.routing` in config/packages/*.yaml."""
        out = []
        for f in sorted(self.root.glob("config/packages/**/*.yaml")) + sorted(self.root.glob("config/packages/*.yml")):
            try:
                t = f.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if "routing:" not in t or "messenger" not in t:
                continue
            rel = str(f.relative_to(self.root))
            lines = t.split("\n")
            for i, ln in enumerate(lines):
                m = re.match(r"^(\s*)routing:\s*$", ln)
                if not m:
                    continue
                ind = len(m.group(1))
                for j in range(i + 1, len(lines)):
                    r = lines[j]
                    if r.strip() == "" or r.lstrip().startswith("#"):
                        continue
                    if len(r) - len(r.lstrip()) <= ind:
                        break
                    e = re.match(r"^\s*['\"]?\\?([\w\\*]+)['\"]?\s*:\s*(.*)$", r)
                    if not e or "\\" not in e.group(1):
                        continue
                    trs = re.findall(r"[\w.-]+", e.group(2).split("#")[0])
                    for tr in trs:
                        out.append((e.group(1).replace("\\\\", "\\"), tr, rel, j + 1))
        return out

    # -------------------------------------------------------------- worker processes
    WORKER = (("celery", re.compile(r"\bcelery\b[^\n]*?\bworker\b([^\n]*)")),
              ("rq", re.compile(r"\b(?:rq\s+worker|rqworker)\b([^\n]*)")),
              ("dramatiq", re.compile(r"(?:^|[\s/])dramatiq\s+([^\n]*)")),
              ("laravel", re.compile(r"\bartisan\s+queue:(?:work|listen)\b([^\n]*)")),
              ("messenger", re.compile(r"\bmessenger:consume\b([^\n]*)")))

    def workers(self):
        fws = {fw for fw, _n in self.tasks} | self.fws | self.uses
        if not fws:
            return
        skip = presets.skip_dirs("python")
        for dp, dns, fns in os.walk(self.root):
            dns[:] = sorted(d for d in dns if d not in skip and not d.startswith("."))
            for fn in fns:
                rel = os.path.relpath(os.path.join(dp, fn), self.root)
                if not PROC_FILE.search(rel):
                    continue
                try:
                    t = (self.root / rel).read_text(encoding="utf-8", errors="replace")[:200000]
                except OSError:
                    continue
                self._worker_file(rel, t, fws)

    def _worker_file(self, rel, t, fws):
        lines = t.split("\n")
        for i, raw in enumerate(lines):
            if raw.lstrip().startswith("#") or re.match(r"\s*(?:keywords|classifiers|dependencies|requires|description|module)\s*=", raw):
                continue
            line = re.sub(r",\s|,$", " ", re.sub(r"[\"'\[\]]", " ", raw))
            for fw, rx in self.WORKER:
                if fw not in fws:
                    continue
                m = rx.search(line)
                if not m:
                    continue
                rest = m.group(1)
                if fw in ("rq", "messenger"):
                    qs, toks = [], re.split(r"\s+", re.split(r"&&|\|\||;|\||>", rest)[0].strip())
                    i2 = 0
                    while i2 < len(toks):
                        x = toks[i2]
                        i2 += 1
                        if x.startswith("-"):
                            if "=" not in x and x not in RQ_FLAGS:
                                i2 += 1                     # the option's value
                        elif re.fullmatch(r"[\w.:-]+", x):
                            qs.append(x)
                else:
                    q = re.search(r"(?:-Q|--queues?)[= ]?\s*([\w.,:${}-]+)", rest)
                    qs = q.group(1).split(",") if q else []
                    if fw == "dramatiq" and not re.match(r"\s*(?:--?[\w-]+(?:=\S+|\s+\d+)?\s+)*[A-Za-z_]\w*(?:\.\w+)+(?::\w+)?(?:\s|$)|.*\s(?:-Q|--queues)\b", rest):
                        continue                      # `dramatiq app.tasks [-Q q]`, not the word in prose or metadata
                qs = [q for q in qs if q and "$" not in q] or [self.defaults.get(fw, DEFAULT_QUEUE[fw])]
                proc = self._proc_name(rel, lines, i)
                for q in qs:
                    self.consumers[(fw, q)].append({"process": proc, "at": f"{rel}:{i + 1}"})
                self.st[f"{fw}_worker_processes"] += 1

    @staticmethod
    def _proc_name(rel, lines, i):
        base = os.path.basename(rel)
        if base.startswith("Procfile"):
            m = re.match(r"^([\w-]+)\s*:", lines[i])
            return m.group(1) if m else base
        if re.search(r"compose[\w.-]*\.ya?ml$", base):
            for j in range(i, -1, -1):
                m = re.match(r"^  ([\w.-]+):\s*$", lines[j])
                if m:
                    return m.group(1)
        if base.endswith(".conf"):
            for j in range(i, -1, -1):
                m = re.match(r"^\[program:([\w.-]+)\]", lines[j])
                if m:
                    return m.group(1)
        return base

    def attach_consumers(self):
        from .protocols import _endpoint
        known = bool(self.consumers)
        for (fw, q), cs in self.consumers.items():
            _endpoint(self.b, "queue", f"{fw}/{q}", {"framework": fw, "queue": q})
            self.queues_used.add((fw, q))
        for fw, q in self.queues_used:
            ep = self.b.nodes.get(f"endpoint:queue:{fw}/{q}")
            if ep is None:
                continue
            cs = self.consumers.get((fw, q)) or []
            ep.attrs["consumers"] = sorted({c["process"] for c in cs}) or None
            ep.attrs["consumers_at"] = [c["at"] for c in cs][:20] or None
            if cs:
                ep.attrs["served"] = "worker process"   # a worker takes any job off it: received (as GraphQL served fields)
            if known and any(k[0] == fw for k in self.consumers):
                ep.attrs["workers_known"] = True     # some worker process of this framework is in the repo
            if ep.attrs.get("consumers") is None:
                ep.attrs.pop("consumers", None)
                ep.attrs.pop("consumers_at", None)
        # the queue of every job endpoint without one: the framework's default queue
        for (fw, name), t in self.tasks.items():
            ep = self.b.nodes[t["nid"]]
            ep.attrs.setdefault("queue", t["queue"] or self.defaults.get(fw))
            q = ep.attrs.get("queue")
            if q and self.consumers.get((fw, q)):
                ep.attrs["processes"] = sorted({c["process"] for c in self.consumers[(fw, q)]})

    def run(self) -> dict:
        if any(f.endswith(".py") for f in self.s.files):
            self.celery()
            self.rq()
            self.dramatiq()
        if any(f.endswith(JS_EXT) for f in self.s.files):
            self.bull()
        self.twins()
        if any(f.endswith(".php") for f in self.s.files):
            self.laravel()
            self.messenger()
        if not self.tasks and not self.queues_used and not self.fws:
            return {}
        self.workers()
        self.attach_consumers()
        out = {k: v for k, v in self.st.items() if v}
        if not out:
            return {}
        out["queues"] = len(self.queues_used)
        if self.samples:
            out["samples"] = dict(self.samples)
        return out


def apply(project, builder, sock=None) -> dict:
    """Job / queue endpoints of the project's background-job frameworks (#36); empty without any."""
    return Scan(project, builder, sock).run()
