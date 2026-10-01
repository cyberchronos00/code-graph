"""Django settings, admin, signals, Celery, Channels dispatch and management commands (functions taking the
DjangoPlugin instance `p`; split out of plugin.py to keep the url/route code readable)."""
from __future__ import annotations

import ast

from ...core.model import EXACT, HEURISTIC, RESOLVED
from ..python.plugin import Ctx, PythonPlugin, ann_text, const_str, dotted, kwarg, walk_body

MODEL_SIGNALS = {"pre_save": "save", "post_save": "save", "pre_delete": "delete", "post_delete": "delete"}


def mod_of(path):
    return ("/".join(path.split("/")[:-1]) or None) if path else None


# ------------------------------------------------------------------ settings
def settings_modules(p):
    prog = p.prog
    names = []
    for m in prog.modules.values():
        for sub in ast.walk(m.tree):
            if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute) and sub.func.attr == "setdefault" \
                    and len(sub.args) >= 2 and const_str(sub.args[0]) == "DJANGO_SETTINGS_MODULE" and const_str(sub.args[1]):
                names.append(const_str(sub.args[1]))
    mods = []
    for n in names:
        m = prog.module(n)
        if m and m not in mods:
            mods.append(m)
    if not mods:
        mods = [m for m in prog.modules.values() if m.name.split(".")[-1] == "settings" or
                (len(m.name.split(".")) > 1 and m.name.split(".")[-2] == "settings")]
    out, stack = [], list(mods)
    while stack:
        m = stack.pop(0)
        if m in out:
            continue
        out.append(m)
        for s in m.star:
            sm = prog.module(s)
            if sm:
                stack.append(sm)
    return out


def settings(p) -> dict:
    prog, b = p.prog, p.b
    p.settings_mods = settings_modules(p)
    p.settings_values = {}
    n = 0
    for m in p.settings_mods:
        for stt in m.tree.body:
            tgts = []
            if isinstance(stt, ast.Assign):
                tgts = [t.id for t in stt.targets if isinstance(t, ast.Name)]
            elif isinstance(stt, ast.AnnAssign) and isinstance(stt.target, ast.Name):
                tgts = [stt.target.id]
            for k in tgts:
                if not k.isupper():
                    continue
                cid = b.add_node("config", f"settings.{k}", name=f"settings.{k}", fqn=f"settings.{k}", file=m.file,
                                 line=stt.lineno, module="settings", lang="python")
                p.settings_values.setdefault(k, []).append((m, stt.value))
                n += 1
                ctx = Ctx(m, None, None)
                for sub in (ast.walk(stt.value) if stt.value is not None else []):
                    if isinstance(sub, ast.Call):
                        r = PythonPlugin.env_key(prog, sub, ctx)
                        if r:
                            b.add_edge(cid, b.add_node("env", r[0], lang="env"), "READS_ENV", m.file, sub.lineno, EXACT, via=r[1])
                    elif isinstance(sub, ast.Subscript) and const_str(sub.slice):
                        t = prog.infer(sub.value, ctx)
                        if t and t[0] == "ext" and t[1] == "os.environ":
                            b.add_edge(cid, b.add_node("env", const_str(sub.slice), lang="env"), "READS_ENV", m.file, sub.lineno, EXACT, via="os.environ[]")
                    elif isinstance(sub, ast.Name) and sub.id.isupper() and sub.id != k:
                        b.add_edge(cid, f"config:settings.{sub.id}", "REFERS_TO", m.file, sub.lineno, RESOLVED)
    return {"modules": [m.name for m in p.settings_mods], "keys": n}


def settings_reads(p) -> int:
    prog, b = p.prog, p.b
    n = 0
    smods = {m.name for m in p.settings_mods}
    for f in prog.funcs.values():
        ctx = Ctx(f.module, f, f.cls)
        for sub in walk_body(f.node):
            key = None
            if isinstance(sub, ast.Attribute) and sub.attr.isupper() and isinstance(sub.value, (ast.Name, ast.Attribute)):
                t = prog.infer(sub.value, ctx)
                if t and ((t[0] == "ext" and t[1] == "django.conf.settings") or (t[0] == "mod" and t[1].name in smods)):
                    key = sub.attr
            elif isinstance(sub, ast.Call) and isinstance(sub.func, ast.Name) and sub.func.id in ("getattr", "hasattr") and len(sub.args) >= 2:
                t = prog.infer(sub.args[0], ctx)
                if t and t[0] == "ext" and t[1] == "django.conf.settings" and const_str(sub.args[1]):
                    key = const_str(sub.args[1])
            if key:
                cid = f"config:settings.{key}"
                if cid not in b.nodes:
                    b.add_node("config", f"settings.{key}", name=f"settings.{key}", lang="python", attrs={"not_in_settings": True})
                b.add_edge(f.id, cid, "READS_CONFIG", f.file, sub.lineno, EXACT)
                n += 1
    return n


# ------------------------------------------------------------------ admin
def admin(p) -> dict:
    prog, b = p.prog, p.b
    regs = []
    for m in prog.modules.values():
        for sub in ast.walk(m.tree):
            if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute) and sub.func.attr == "register" and sub.args:
                base = ann_text(sub.func.value) or ""
                t = prog.infer(sub.func.value, Ctx(m, None, None))
                is_site = base.endswith("site") or (t and t[0] in ("ext", "einst") and "admin" in t[1]) or \
                    (t and t[0] == "inst" and prog.subclass_of(t[1], "AdminSite"))
                if not is_site:
                    continue
                targets = sub.args[0].elts if isinstance(sub.args[0], (ast.List, ast.Tuple)) else [sub.args[0]]
                for tg in targets:
                    regs.append((m, tg, sub.args[1] if len(sub.args) > 1 else None, sub.lineno))
        for c in m.all_classes:
            for d in c.decorators:
                dn = dotted(d.func) if isinstance(d, ast.Call) else None
                if dn and dn.split(".")[-1] == "register" and ("admin" in dn or dn == "register"):
                    for tg in d.args:
                        regs.append((m, tg, ("cls", c), d.lineno))
    n = 0
    for m, tg, adm, line in regs:
        mt = prog.infer(tg, Ctx(m, None, None))
        model = mt[1] if mt and mt[0] == "type" else None
        ac = adm[1] if isinstance(adm, tuple) else None
        if adm is not None and ac is None:
            at = prog.infer(adm, Ctx(m, None, None))
            ac = at[1] if at and at[0] == "type" else None
        key = ac.qual if ac else (model.qual if model else ann_text(tg))
        aid = b.add_node("admin", key, name=(ac.name if ac else f"admin:{model.name if model else key}"), fqn=key,
                         file=m.file, line=line, module=mod_of(m.file), lang="python", entry_kind="admin_panel",
                         attrs={"model": model.qual if model else None, "framework": "django-admin"})
        n += 1
        tbl = p.models.table(model.qual) if model else None
        if tbl:
            b.add_edge(aid, f"table:{tbl}", "READS_TABLE", m.file, line, RESOLVED, via="django-admin")
            b.add_edge(aid, f"table:{tbl}", "WRITES_TABLE", m.file, line, RESOLVED, via="django-admin")
        if not ac:
            continue
        for f in ac.methods.values():
            b.add_edge(aid, f.id, "HANDLED_BY", f.file, f.line, RESOLVED, via="admin-surface")
        acts = ac.attrs.get("actions")
        if acts and isinstance(acts[0], (ast.List, ast.Tuple)):
            for x in acts[0].elts:
                t = prog.infer(x, Ctx(ac.module, None, ac))
                if t and t[0] in ("func", "bound"):
                    b.add_edge(aid, t[1].id, "HANDLED_BY", ac.file, acts[1], RESOLVED, via="admin-action")
        inl = ac.attrs.get("inlines")
        if inl and isinstance(inl[0], (ast.List, ast.Tuple)):
            for x in inl[0].elts:
                t = prog.infer(x, Ctx(ac.module, None, ac))
                if t and t[0] == "type":
                    mm = p.class_attr_expr(t[1], "model")
                    if mm:
                        t2 = prog.infer(mm[0], Ctx(mm[1].module, None, mm[1]))
                        if t2 and t2[0] == "type" and p.models.table(t2[1].qual):
                            b.add_edge(aid, f"table:{p.models.table(t2[1].qual)}", "WRITES_TABLE", ac.file, inl[1], RESOLVED, via="admin-inline")
    return {"registrations": n}


# ------------------------------------------------------------------ signals
def signal_key(p, m, e, custom, only_custom=False):
    prog = p.prog
    if isinstance(e, ast.Name):
        r = prog.resolve_name(m, e.id)
        if r and r[0] == "var" and (r[1].name, r[2]) in custom:
            return f"{r[1].name}.{r[2]}"
    if isinstance(e, ast.Attribute):
        t0 = prog.infer(e.value, Ctx(m, None, None))
        if t0 and t0[0] == "mod" and (t0[1].name, e.attr) in custom:
            return f"{t0[1].name}.{e.attr}"
    if only_custom:
        return None
    t = prog.infer(e, Ctx(m, None, None))
    if t and t[0] == "ext":
        return t[1]
    return None


def signals(p) -> dict:
    prog, b = p.prog, p.b
    custom = {}
    for m in prog.modules.values():
        for name, vals in m.vars.items():
            for v, line, _ in vals:
                if isinstance(v, ast.Call):
                    t = prog.infer(v.func, Ctx(m, None, None))
                    if t and t[0] == "ext" and t[1].split(".")[-1] in ("Signal", "ModelSignal"):
                        custom[(m.name, name)] = (m, line)
    conns = []
    for f in prog.funcs.values():
        for d in f.decorators:
            if isinstance(d, ast.Call) and (dotted(d.func) or "").split(".")[-1] == "receiver" and d.args:
                sigs = d.args[0].elts if isinstance(d.args[0], (ast.List, ast.Tuple)) else [d.args[0]]
                for s in sigs:
                    conns.append((f.module, s, f, kwarg(d, "sender"), d.lineno, EXACT))
    for m in prog.modules.values():
        for sub in ast.walk(m.tree):
            if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute) and sub.func.attr == "connect" and sub.args:
                ht = prog.infer(sub.args[0], Ctx(m, None, None))
                if ht and ht[0] in ("func", "bound"):
                    conns.append((m, sub.func.value, ht[1], kwarg(sub, "sender"), sub.lineno, RESOLVED))
    n = 0
    writes = [e for e in b.edges.values() if e.kind == "WRITES_TABLE"]
    for m, sig, h, sender, line, conf in conns:
        sk = signal_key(p, m, sig, custom)
        if not sk or ("signal" not in sk and not any(sk == f"{a}.{c}" for a, c in custom)):
            continue
        sname, sender_q = sk, None
        if sender is not None:
            st = prog.infer(sender, Ctx(m, None, None))
            if st and st[0] == "type":
                sender_q = st[1].qual
                sname = f"{sk}[{st[1].name}]"
            elif const_str(sender):
                sname = f"{sk}[{const_str(sender)}]"
        eid = b.add_node("event", sname, name=sname, fqn=sname, lang="python", attrs={"signal": sk, "sender": sender_q})
        b.add_edge(eid, h.id, "LISTENED_BY", m.file, line, conf)
        lid = b.add_node("listener", h.qual, name=h.name, fqn=h.qual, file=h.file, line=h.line, module=mod_of(h.file),
                         lang="python", entry_kind="listener", attrs={"signal": sname})
        b.add_edge(lid, h.id, "HANDLED_BY", h.file, h.line, EXACT)
        n += 1
        short = sk.split(".")[-1]
        if sender_q and short in MODEL_SIGNALS and p.models.table(sender_q):
            tid = f"table:{p.models.table(sender_q)}"
            want = MODEL_SIGNALS[short]
            fire = {"save", "create", "get_or_create", "update_or_create"} if want == "save" else {"delete"}
            for e in writes:
                via = (e.attrs.get("via") or "").split(".")[-1]
                if e.dst == tid and (via in fire or (via.startswith("a") and via[1:] in fire)):
                    b.add_edge(e.src, eid, "DISPATCHES", e.file, e.line, RESOLVED, via=f"{e.attrs.get('via')} fires {short}")
    for f in prog.funcs.values():
        for sub in walk_body(f.node):
            if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute) and sub.func.attr in ("send", "send_robust", "asend", "asend_robust"):
                sk = signal_key(p, f.module, sub.func.value, custom, only_custom=True)
                if sk:
                    for eid in [x for x in b.nodes if x == f"event:{sk}" or x.startswith(f"event:{sk}[")]:
                        b.add_edge(f.id, eid, "DISPATCHES", f.file, sub.lineno, RESOLVED, via="Signal.send")
    return {"receivers": n, "custom_signals": len(custom)}


# ------------------------------------------------------------------ celery
def celery(p) -> dict:
    prog, b = p.prog, p.b
    tasks, by_name = {}, {}
    for f in prog.funcs.values():
        for d in f.decorators:
            dn = dotted(d.func if isinstance(d, ast.Call) else d) or ""
            last = dn.split(".")[-1]
            if last in ("shared_task", "periodic_task", "db_task", "db_periodic_task") or (last == "task" and dn != "task") or dn == "task" and "celery" in str(f.module.imports.get("task", "")):
                tname = (const_str(kwarg(d, "name")) if isinstance(d, ast.Call) else None) or f.qual
                jid = b.add_node("job", f.qual, name=f.name, fqn=f.qual, file=f.file, line=f.line, module=mod_of(f.file),
                                 lang="python", entry_kind="queue_job", attrs={"task_name": tname, "decorator": dn})
                b.add_edge(jid, f.id, "HANDLED_BY", f.file, f.line, EXACT)
                tasks[f.qual] = jid
                by_name[tname] = jid
                if last in ("periodic_task", "db_periodic_task"):
                    sid = b.add_node("schedule", f"{f.qual}@{f.file}:{d.lineno}", name=f"schedule {f.name}", file=f.file,
                                     line=d.lineno, lang="python", entry_kind="scheduled")
                    b.add_edge(sid, jid, "SCHEDULES", f.file, d.lineno, EXACT)
                break
    for c in prog.classes.values():
        if prog.subclass_of(c, "celery.Task", "celery.app.task.Task") and "run" in c.methods:
            f = c.methods["run"]
            jid = b.add_node("job", c.qual, name=c.name, fqn=c.qual, file=c.file, line=c.line, lang="python", entry_kind="queue_job")
            b.add_edge(jid, f.id, "HANDLED_BY", f.file, f.line, EXACT)
            tasks[c.qual] = jid
    n_d = 0
    for f in prog.funcs.values():
        ctx = Ctx(f.module, f, f.cls)
        for sub in walk_body(f.node):
            if not (isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute)):
                continue
            a = sub.func.attr
            if a in ("delay", "apply_async", "s", "si", "signature", "apply", "map", "starmap", "chunks", "delay_on_commit"):
                t = prog.infer(sub.func.value, ctx)
                q = t[1].qual if t and t[0] in ("func", "type") else None
                if q in tasks:
                    b.add_edge(f.id, tasks[q], "DISPATCHES", f.file, sub.lineno, EXACT if t[0] == "func" else RESOLVED, via=f".{a}()")
                    n_d += 1
            elif a == "send_task" and sub.args and const_str(sub.args[0]) in by_name:
                b.add_edge(f.id, by_name[const_str(sub.args[0])], "DISPATCHES", f.file, sub.lineno, RESOLVED, via="send_task")
                n_d += 1
    n_s = 0
    for m in prog.modules.values():
        for sub in ast.walk(m.tree):
            dct = None
            if isinstance(sub, ast.Assign):
                tn = ann_text(sub.targets[0]) or ""
                if tn in ("CELERY_BEAT_SCHEDULE", "CELERYBEAT_SCHEDULE", "beat_schedule") or tn.endswith(".beat_schedule"):
                    dct = sub.value
            elif isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute) and sub.func.attr == "update":
                dct = kwarg(sub, "beat_schedule")
            if isinstance(dct, ast.Dict):
                for k, v in zip(dct.keys, dct.values):
                    if not isinstance(v, ast.Dict):
                        continue
                    for k2, v2 in zip(v.keys, v.values):
                        if const_str(k2) == "task" and const_str(v2):
                            tn2 = const_str(v2)
                            jid = by_name.get(tn2) or tasks.get(tn2) or b.add_node("job", tn2, lang="python", attrs={"unresolved_task": True})
                            sid = b.add_node("schedule", f"{const_str(k) or tn2}@{m.file}:{v.lineno}", name=f"beat {const_str(k) or tn2}",
                                             file=m.file, line=v.lineno, lang="python", entry_kind="scheduled")
                            b.add_edge(sid, jid, "SCHEDULES", m.file, v2.lineno, EXACT if (tn2 in by_name or tn2 in tasks) else HEURISTIC)
                            n_s += 1
            if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute) and sub.func.attr == "add_periodic_task" and len(sub.args) >= 2:
                sig = sub.args[1]
                if isinstance(sig, ast.Call) and isinstance(sig.func, ast.Attribute):
                    t = prog.infer(sig.func.value, Ctx(m, None, None))
                    if t and t[0] == "func" and t[1].qual in tasks:
                        sid = b.add_node("schedule", f"{t[1].qual}@{m.file}:{sub.lineno}", name=f"periodic {t[1].name}", file=m.file,
                                         line=sub.lineno, lang="python", entry_kind="scheduled")
                        b.add_edge(sid, tasks[t[1].qual], "SCHEDULES", m.file, sub.lineno, EXACT)
                        n_s += 1
    return {"tasks": len(tasks), "dispatches": n_d, "schedules": n_s}


def group_sends(p) -> int:
    """channel_layer.group_send(group, {"type": "chat.message"}) -> consumer method chat_message."""
    from .plugin import CONSUMER_BASES
    prog, b = p.prog, p.b
    cons = {}
    for c in prog.classes.values():
        if prog.subclass_of(c, *CONSUMER_BASES):
            for mn, mf in c.methods.items():
                cons.setdefault(mn, []).append(mf)
    n = 0
    for f in prog.funcs.values():
        for sub in walk_body(f.node):
            if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute) and sub.func.attr in ("group_send", "send") \
                    and len(sub.args) >= 2 and isinstance(sub.args[1], ast.Dict):
                for k, v in zip(sub.args[1].keys, sub.args[1].values):
                    if const_str(k) == "type" and const_str(v):
                        hn = const_str(v).replace(".", "_")
                        for hf in cons.get(hn, []):
                            b.add_edge(f.id, hf.id, "DISPATCHES", f.file, sub.lineno, RESOLVED if len(cons[hn]) == 1 else HEURISTIC,
                                       via=f"channel_layer.{sub.func.attr} type={const_str(v)}")
                            n += 1
    return n


# ------------------------------------------------------------------ management commands
def commands(p) -> dict:
    prog, b = p.prog, p.b
    cmds = {}
    for m in prog.modules.values():
        parts = m.file.split("/")
        if len(parts) >= 3 and parts[-2] == "commands" and parts[-3] == "management" and not parts[-1].startswith("_"):
            c = m.classes.get("Command")
            if not c:
                continue
            name = parts[-1][:-3]
            cid = b.add_node("command", name, name=name, fqn=c.qual, file=m.file, line=c.line, module=mod_of(m.file),
                             lang="python", entry_kind="management_command",
                             doc=const_str((c.attrs.get("help") or (None,))[0]), attrs={"framework": "django"})
            h = prog.find_method(c, "handle")
            if h:
                b.add_edge(cid, h.id, "HANDLED_BY", h.file, h.line, EXACT)
            cmds[name] = cid
    n = 0
    for f in prog.funcs.values():
        ctx = Ctx(f.module, f, f.cls)
        for sub in walk_body(f.node):
            if isinstance(sub, ast.Call) and sub.args:
                t = prog.infer(sub.func, ctx)
                if t and t[0] == "ext" and t[1].endswith("call_command") and const_str(sub.args[0]):
                    nm = const_str(sub.args[0])
                    b.add_edge(f.id, cmds.get(nm) or b.add_node("command", nm, lang="python"), "DISPATCHES", f.file, sub.lineno,
                               EXACT if nm in cmds else HEURISTIC, via="call_command")
                    n += 1
    return {"commands": len(cmds), "call_command": n}
