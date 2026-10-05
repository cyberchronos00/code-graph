"""Android IPC between components (#38 part 3): explicit and implicit intents and AIDL services.

  intent         endpoint:intent:<component class>   senders: `Intent(ctx, Foo::class.java)` / `new Intent(ctx,
                                 Foo.class)`, `setClass(ctx, Foo::class.java)`, `ComponentName(ctx, Foo::class.java)` and
                                 `ComponentName(pkg, "com.x.Foo")` / `setClassName(pkg, "com.x.Foo")` naming an in-repo
                                 class; `via` the consuming call (`startActivity`, `startService`, `bindService`,
                                 `sendBroadcast`, `PendingIntent.getBroadcast` ...). Receiver: the component's entry
                                 method (`onReceive`, `onStartCommand` / `onBind` / `onHandleIntent`, `onCreate`), else the
                                 class.
  intent-action  endpoint:intent-action:<action>     senders: `Intent("com.x.ACTION")`, `setAction(..)` /
                                 `action = ..` on an intent; receivers: manifest `<intent-filter><action>` on a
                                 component, `IntentFilter(..)` / `addAction(..)` registered in code (the file's
                                 `onReceive`). Platform actions (`android.*`, `Intent.ACTION_*`) are skipped.
  aidl           endpoint:aidl:<package.IFace>.<method>   the methods of each `.aidl` interface: implementations in
                                 `object : IFace.Stub()` / `class X : IFace.Stub()` / `extends IFace.Stub` receive;
                                 calls of those methods in other code that names `IFace` send.
"""
from __future__ import annotations

import os
import re
import xml.etree.ElementTree as ET
from collections import defaultdict

from . import presets
from .brokers import Scan
from .core.model import EXACT, HEURISTIC, RESOLVED
from .protocols import protocol_receive, protocol_send

JVM_EXT = (".kt", ".java")
ANS = "{http://schemas.android.com/apk/res/android}"
ENTRY = {"receiver": ("onReceive",), "service": ("onStartCommand", "onBind", "onHandleIntent", "onHandleWork", "onMessageReceived",
                                           "onNewToken", "onCreate"),
         "activity": ("onCreate", "onNewIntent"), "activity-alias": ("onCreate",), "provider": ("onCreate",)}
SUPER = (("receiver", r"BroadcastReceiver"), ("service", r"Service|JobIntentService"), ("activity", r"Activity"))
CLS = r"(\w+)(?:::class\.java|\.class)"
CONSUMERS = re.compile(r"\b(startActivity|startActivityForResult|startService|startForegroundService|bindService|"
                       r"sendBroadcast|sendOrderedBroadcast|PendingIntent(?:Compat)?\.get(?:Activity|Service|ForegroundService|Broadcast)|"
                       r"setResult|launch|startActivities|enqueueWork)\b")
PLATFORM = re.compile(r"^(?:android|com\.android|com\.google\.android|com\.google\.firebase|androidx)\.")
# a call stubbed or verified on a mock, not made: `every { m.x(..) }`, `coVerify { .. }`, `whenever(m.x(..))`, `verify(m).x(`
MOCK = re.compile(r"\b(?:every|coEvery|verify|coVerify|verifyOrder|verifySequence)\s*(?:\([^()]*\))?\s*\{[^{}]*$|"
                  r"\b(?:when|whenever|`when`)\s*\([^()]*$|\bverify\s*\([^()]*\)\s*\.\s*$")
# a value compared in an assertion, not an intent being sent: `assertThat(i.component).isEqualTo(ComponentName(..))`
ASSERT = re.compile(r"\b(?:assert\w*|isEqualTo|isNotEqualTo|shouldBe|shouldEqual|expect|containsExactly)\s*\(")
SKIP_DIRS = presets.skip_dirs("common", "scan_skip_dirs")


def _strip_comments(t):
    return re.sub(r"/\*[\s\S]*?\*/|//[^\n]*", "", t)


class AndroidIpc(Scan):
    def run(self) -> dict:
        self.jvm = [f for f in sorted(self.s.files) if f.endswith(JVM_EXT)]
        if not self.jvm:
            return {}
        self.classes = {}                          # fqn -> class nid
        self.short = defaultdict(list)             # short name -> [fqn]
        self.class_spans = defaultdict(list)       # file -> [(line, end, class nid)]
        for nid, n in self.b.nodes.items():
            if n.kind == "class" and n.file and n.file.endswith(JVM_EXT):
                fq = getattr(n, "fqn", None) or nid.split(":", 1)[1]
                self.classes[fq] = nid
                self.short[fq.rsplit(".", 1)[-1]].append(fq)
                if n.line:
                    self.class_spans[n.file].append((n.line, n.end_line or n.line, nid))
        self.read_manifests()
        for f in self.jvm:
            src = self.s.text(f) or ""
            if "Intent" in src or "ComponentName" in src:
                self.intents(f, src)
        self.manifests()
        self.aidl()
        out = {k: v for k, v in self.st.items() if v}
        if out and self.samples:
            out["samples"] = dict(self.samples)
        return out

    # ---------------------------------------------------------------- helpers
    def fn_at(self, file, pos):
        """The function around `pos`, else the narrowest class around it (constructor default lambdas, property
        initialisers), else the file."""
        fn, _lo, _hi = self.s.fn_bounds(file, pos)
        if fn:
            return fn
        line = self.s.line_of(file, pos)
        inside = [(e - l, nid) for l, e, nid in self.class_spans.get(file, ()) if l <= line <= e]
        return min(inside)[1] if inside else self.module_of(file)

    def resolve_class(self, f, src, name):
        """The in-repo class `name` refers to in file `f` (import, same package, else a unique short name)."""
        m = re.search(rf"(?m)^import\s+([\w.]+)\.{name}\s*;?\s*$", src)
        if m:
            return f"{m.group(1)}.{name}" if f"{m.group(1)}.{name}" in self.classes else None
        p = re.search(r"(?m)^package\s+([\w.]+)", src)
        if p and f"{p.group(1)}.{name}" in self.classes:
            return f"{p.group(1)}.{name}"
        c = self.short.get(name) or []
        return c[0] if len(c) == 1 else None

    def kind_of(self, fq):
        nid = self.classes.get(fq)
        n = self.b.nodes.get(nid) if nid else None
        if n is None:
            return None
        k = (n.attrs or {}).get("android_component")
        if k:
            return k
        src = self.s.text(n.file) or ""
        short = fq.rsplit(".", 1)[-1]
        m = re.search(rf"\bclass\s+{short}\b[^{{]*?(?::|extends)\s*([\w.]+)", src)
        if m:
            for kind, rx in SUPER:
                if re.search(rx, m.group(1)):
                    return kind
        return None

    def entry(self, fq):
        kind = self.kind_of(fq) or ""
        for m in ENTRY.get(kind, ()):
            nid = f"method:{fq}.{m}"
            if nid in self.b.nodes and not (m == "onBind" and self.null_body(self.b.nodes[nid])):
                return nid
        return self.classes.get(fq)

    def null_body(self, n):
        """`onBind(..) = null` / `{ return null }`: a started-only service, so onBind is not its entry."""
        lines = (self.s.text(n.file) or "").splitlines()[(n.line or 1) - 1:(n.end_line or n.line or 1)]
        body = " ".join(lines)
        body = body[body.find(")") + 1:] if ")" in body else body
        return re.fullmatch(r"\s*(?::\s*[\w.?<>]+\s*)?(?:=\s*null|\{\s*(?:return\s+null\s*;?)?\s*\})\s*", body) is not None

    def consumer(self, f, src, pos):
        """The call that uses the intent built at `pos`: the enclosing call on the same line
        (`startService(Intent(..))`), else the next one in the same function (`val i = Intent(..); bindService(i, ..)`).
        Only real calls count: not words in comments / strings, not a coroutine `launch { }`."""
        def calls(lo, hi):
            return [m for m in CONSUMERS.finditer(src, lo, hi)
                    if re.match(r"\s*\(", src[m.end():m.end() + 20]) and not self.s.masked(f, m.start())]
        bol = src.rfind("\n", 0, pos) + 1
        before = calls(bol, pos)
        if before:
            return before[-1].group(1)
        # an enclosing call opened on an earlier line: `PendingIntent.getActivity(\n ctx,\n Intent(..))`
        for m in reversed(calls(max(0, pos - 400), bol)):
            seg = src[m.end():pos]
            if seg.count("(") > seg.count(")"):
                return m.group(1)
        _fn, _lo, hi = self.s.fn_bounds(f, pos)
        after = calls(pos, min(hi or len(src), pos + 800, len(src)))
        return after[0].group(1) if after else None

    # ---------------------------------------------------------------- intents in code
    def intents(self, f, src):
        rxs = ((rf"\bIntent\s*\(\s*[^,()]+(?:\([^()]*\))?\s*,\s*{CLS}\s*,?\s*\)", "Intent(ctx, X)"),
               (rf"\bsetClass\s*\(\s*[^,()]+(?:\([^()]*\))?\s*,\s*{CLS}\s*,?\s*\)", "setClass"),
               (rf"\bComponentName\s*\(\s*[^,()]+(?:\([^()]*\))?\s*,\s*{CLS}\s*,?\s*\)", "ComponentName(ctx, X)"))
        for rx, how in rxs:
            for m in re.finditer(rx, src):
                if self.s.masked(f, m.start()):
                    continue
                fq = self.resolve_class(f, src, m.group(1))
                if fq:
                    self.send_intent(f, src, m.start(), fq, how, EXACT)
        for m in re.finditer(r"\b(ComponentName|setClassName)\s*\(\s*([^,()]+(?:\([^()]*\))?)\s*,\s*([^,()]+?)\s*,?\s*\)", src):
            v, _c = self.value(f, m.start(), m.group(3))
            if v and v in self.classes:
                bol = src.rfind("\n", 0, m.start()) + 1
                if not (self.consumer(f, src, m.start()) or m.group(1) == "setClassName"
                        or re.search(r"\b(?:setComponent\s*\(|component\s*=)", src[bol:m.start()])):
                    self.miss("component_name_unused", f"{f}:{self.s.line_of(f, m.start())} {v}")
                    continue                       # a name built for a package-manager shadow / comparison, not sent
                self.send_intent(f, src, m.start(), v, f"{m.group(1)}(pkg, name)", RESOLVED)
            elif v and re.fullmatch(r"[\w.]+\.[A-Z]\w+", v):
                self.miss("intent_component_not_in_repo", f"{f}:{self.s.line_of(f, m.start())} {v}")
        # implicit: Intent("com.x.ACTION"), setAction(X), action = X
        for m in re.finditer(r"\bIntent\s*\(\s*([^,()]+?)\s*(?:,\s*[^()]*)?\)|\bsetAction\s*\(\s*([^()]+?)\s*\)|"
                             r"(?<![\w.])action\s*=\s*([^\n;]+)", src):
            e = (m.group(1) or m.group(2) or m.group(3) or "").strip()
            if not e or "::class" in e or e.endswith(".class") or self.s.masked(f, m.start()):
                continue
            if m.group(3) and not re.search(r"\bIntent\s*\(", src[max(0, m.start() - 300):m.start()]):
                continue                               # `action = ..` on something that is not an intent
            if m.group(1) and m.group(0).count(",") and not re.match(r"\s*[\"A-Z]", e):
                continue                               # Intent(ctx, X) forms are handled above
            if re.match(r"(?:Intent|Settings|\w+Manager|\w+Compat|\w*Contract\w*|MediaStore|Telephony)\.", e):
                continue                               # platform action constants
            v, conf = self.value(f, m.start(), e)
            if not v or PLATFORM.match(v) or not re.fullmatch(r"[\w.$-]+\.[\w$-]+", v):
                continue
            self.send_action(f, src, m.start(), v, conf or RESOLVED)
        # dynamic receivers: IntentFilter(X) / addAction(X) in a file with an onReceive
        recv = [r.start() for r in re.finditer(r"\bfun\s+onReceive\s*\(|\bvoid\s+onReceive\s*\(", src)]
        for m in re.finditer(r"\bIntentFilter\s*\(\s*([^,()]+?)\s*[,)]|\baddAction\s*\(\s*([^()]+?)\s*\)", src):
            e = (m.group(1) or m.group(2)).strip()
            if re.match(r"(?:Intent|Settings|\w+Manager|\w+Compat|ConnectivityManager)\.", e):
                continue
            v, conf = self.value(f, m.start(), e)
            if not v or PLATFORM.match(v):
                continue
            h = self.fn_at(f, recv[0] + 4) if len(recv) == 1 else self.fn_at(f, m.start())
            self.recv_action(f, m.start(), v, h, conf or RESOLVED, "IntentFilter")

    @staticmethod
    def in_assertion(src, pos):
        bol = src.rfind("\n", 0, pos) + 1
        for m in ASSERT.finditer(src, max(0, bol - 200), pos):
            depth = 1                              # the assertion's own paren, still open at `pos`?
            for ch in src[m.end():pos]:
                depth += (ch == "(") - (ch == ")")
                if depth == 0:
                    break
            if depth > 0:
                return True
        return False

    def send_intent(self, f, src, pos, fq, how, conf):
        if self.in_assertion(src, pos):
            return
        fn = self.fn_at(f, pos)
        line = self.s.line_of(f, pos)
        key = ("si", fq, fn, line)
        if fn is None or key in self.done:
            return
        self.done.add(key)
        protocol_send(self.b, "intent", fq, fn, f, line, conf, test=self.is_test(f, fn), role="send", how=how,
                      via=self.consumer(f, src, pos), component=self.kind_of(fq))
        self.st["intent_senders"] += 1
        if ("ri", fq) not in self.done:
            self.done.add(("ri", fq))
            n = self.b.nodes[self.classes[fq]]
            h = self.entry(fq)
            if h and not self.is_test(n.file, h):
                protocol_receive(self.b, "intent", fq, h, n.file, self.b.nodes[h].line or n.line, EXACT,
                                 how="component", component=self.kind_of(fq), **self.exposure.get(fq, {}))
                self.st["intent_components"] += 1

    def send_action(self, f, src, pos, action, conf):
        if self.in_assertion(src, pos):
            return
        fn = self.fn_at(f, pos)
        line = self.s.line_of(f, pos)
        key = ("sa", action, fn, line)
        if fn is None or key in self.done:
            return
        self.done.add(key)
        protocol_send(self.b, "intent-action", action, fn, f, line, conf, test=self.is_test(f, fn), role="send",
                      how="intent action", via=self.consumer(f, src, pos))
        self.st["action_senders"] += 1

    def recv_action(self, f, pos, action, h, conf, how, line=None, **exposure):
        if h is None or ("ra", action, h) in self.done or self.is_test(f, h):
            return
        self.done.add(("ra", action, h))
        if line is None:
            line = self.s.line_of(f, pos) if pos is not None else (self.b.nodes[h].line or 0)
        protocol_receive(self.b, "intent-action", action, h, f, line, conf, how=how, **exposure)
        self.st["action_receivers"] += 1

    # ---------------------------------------------------------------- manifests
    def walk(self, pred):
        for dp, dn, fn in os.walk(self.root):
            dn[:] = [d for d in dn if d not in SKIP_DIRS and not d.startswith(".")]
            for x in fn:
                if pred(x):
                    yield os.path.relpath(os.path.join(dp, x), self.root).replace(os.sep, "/")

    def read_manifests(self):
        """Components declared in the repository's AndroidManifest.xml files, with their exposure: `exported`
        (the attribute, else true when the component has an intent filter) and the `android:permission` guarding it
        (the component's, else the application's)."""
        self.mf, self.exposure = [], {}
        for rel in self.walk(lambda x: x == "AndroidManifest.xml"):
            if re.search(r"(?:^|/)(?:test|androidTest)/", rel):
                continue
            try:
                with open(os.path.join(self.root, rel), encoding="utf-8", errors="replace") as fh:
                    text = fh.read()
                root = ET.fromstring(text)
            except (ET.ParseError, OSError, ValueError):
                continue
            pkg = root.get("package")
            app = root.find("application")
            if app is None:
                continue
            for tag in ("receiver", "service", "activity", "activity-alias"):
                for el in app.findall(tag):
                    nm = el.get(ANS + "name") or ""
                    if not nm:
                        continue
                    filters = el.findall("intent-filter")
                    acts = [a.get(ANS + "name") for f_ in filters for a in f_.findall("action")]
                    acts = [a for a in acts if a and not PLATFORM.match(a)]
                    fq = (pkg + nm) if nm.startswith(".") and pkg else nm
                    if fq not in self.classes:
                        c = self.short.get(nm.rsplit(".", 1)[-1]) or []
                        fq = c[0] if len(c) == 1 else None
                    exp = el.get(ANS + "exported")
                    exposure = {"exported": exp == "true" if exp in ("true", "false") else bool(filters),
                                "permission": el.get(ANS + "permission") or app.get(ANS + "permission")}
                    if fq and tag != "activity-alias":
                        self.exposure.setdefault(fq, exposure)
                    self.mf.append((rel, text, tag, nm, fq, acts, exposure))

    def manifests(self):
        for rel, text, tag, nm, fq, acts, exposure in self.mf:
            if not acts:
                continue
            h = self.entry(fq) if fq else None
            if h is None:
                self.miss("manifest_component_unresolved", f"{rel} {nm}")
                continue
            start = self.manifest_pos(text, nm)
            for a in acts:
                m = re.compile(r'android:name\s*=\s*"' + re.escape(a) + '"').search(text, start)
                line = text.count("\n", 0, m.start()) + 1 if m else text.count("\n", 0, start) + 1
                self.recv_action(rel, None, a, h, EXACT, f"AndroidManifest <{tag}> intent-filter", line=line, **exposure)

    @staticmethod
    def manifest_pos(text, nm):
        """Offset of the component's `android:name="<nm>"` (the actions after it belong to it)."""
        m = re.search(r'android:name\s*=\s*"' + re.escape(nm) + '"', text)
        return m.start() if m else 0

    # ---------------------------------------------------------------- AIDL
    def aidl(self):
        ifaces = {}                                     # short name -> (fqn, {methods})
        for rel in self.walk(lambda x: x.endswith(".aidl")):
            try:
                t = _strip_comments(open(os.path.join(self.root, rel), encoding="utf-8", errors="replace").read())
            except OSError:
                continue
            p = re.search(r"\bpackage\s+([\w.]+)\s*;", t)
            m = re.search(r"\b(?:oneway\s+)?interface\s+(\w+)\s*\{([\s\S]*)\}", t)
            if not p or not m:
                continue
            meths = set(re.findall(r"(?m)^\s*(?:oneway\s+)?(?:@\w+\s+)*[\w.<>\[\], ]+?\s+(\w+)\s*\([^)]*\)\s*;", m.group(2)))
            if meths:
                ifaces[m.group(1)] = (f"{p.group(1)}.{m.group(1)}", meths)
                self.st["aidl_interfaces"] += 1
        if not ifaces:
            return
        for f in self.jvm:
            src = self.s.text(f) or ""
            names = [n for n in ifaces if re.search(rf"\b{n}\b", src)]
            if not names:
                continue
            impl_spans = []
            for n in names:
                fq, meths = ifaces[n]
                for st in re.finditer(rf"(?::\s*|extends\s+){n}\.Stub\b(?:\s*\(\s*\))?[^{{]*\{{", src):
                    lo, depth, j = st.end(), 1, st.end()
                    while j < len(src) and depth:
                        depth += {"{": 1, "}": -1}.get(src[j], 0)
                        j += 1
                    impl_spans.append((n, lo, j))
                    for d in re.finditer(r"\b(?:override\s+fun|public\s+[\w<>\[\], .?]+?)\s+(\w+)\s*\(", src[lo:j]):
                        if d.group(1) in meths:
                            pos = lo + d.start(1)
                            h = self.fn_at(f, pos)
                            ep = f"{fq}.{d.group(1)}"
                            if h and ("ra", ep, h) not in self.done and not self.is_test(f, h):
                                self.done.add(("ra", ep, h))
                                protocol_receive(self.b, "aidl", ep, h, f, self.s.line_of(f, pos), EXACT, how="AIDL Stub")
                                self.st["aidl_methods"] += 1
            for n in names:
                fq, meths = ifaces[n]
                if not re.search(rf"\b{n}\b(?!\.Stub\s*\(\s*\))(?![\w.]*\s*\{{)", src):
                    continue
                rx = re.compile(r"(?<![\w])(" + "|".join(sorted(meths, key=len, reverse=True)) + r")\s*\(")
                for c in rx.finditer(src):
                    pos = c.start(1)
                    dotted = src[:pos].rstrip().endswith(".")
                    if self.s.masked(f, pos) or re.search(r"\bfun\s*$|\bvoid\s*$|[\w>\]]\s+$", src[max(0, pos - 40):pos]):
                        continue                         # a definition, not a call
                    if any(nn == n and lo <= pos < hi for nn, lo, hi in impl_spans):
                        continue                         # inside the interface's own implementation
                    if MOCK.search(src[max(0, pos - 160):pos]):
                        continue                         # mockk every { } / verify { }, Mockito when( / verify(x).
                    fn = self.fn_at(f, pos)
                    line = self.s.line_of(f, pos)
                    ep = f"{fq}.{c.group(1)}"
                    if fn is None or ("sa", ep, fn, line) in self.done:
                        continue
                    self.done.add(("sa", ep, fn, line))
                    protocol_send(self.b, "aidl", ep, fn, f, line, RESOLVED if dotted else HEURISTIC,
                                  test=self.is_test(f, fn), role="invoke", how="AIDL proxy call")
                    self.st["aidl_calls"] += 1


def apply(project, builder, sock=None) -> dict:
    """Android intents and AIDL (#38 part 3)."""
    return AndroidIpc(project, builder, sock).run()
