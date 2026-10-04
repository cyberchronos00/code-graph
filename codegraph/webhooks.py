"""Webhook receivers (#37 part 1): routes called by a third party, their signature checks and the events they handle.

For every route, the handler (and the functions it calls or dispatches to, two levels deep, plus its middleware
functions) is scanned:

  verified     a known verification call: Stripe `constructEvent` / `construct_event`, svix / standardwebhooks
               `new Webhook(secret).verify(..)`, @octokit/webhooks `verify` / `verifyAndReceive`, Twilio
               `validateRequest` / `RequestValidator(..).validate`, Shopify `webhooks.validate`; or an HMAC computed
               (`createHmac`, `hmac.new`, `hash_hmac`, ...) next to a constant-time comparison (`timingSafeEqual`,
               `hmac.compare_digest`, `hash_equals`, ...) with a signature header; or a provider token header
               (`X-Gitlab-Token`) compared in constant time; or a route middleware named like a webhook signature
               check (`VerifyWebhookSignature`).
  unverified   the handler reads a provider header (`Stripe-Signature`, `X-GitHub-Event`, `X-Twilio-Signature`, ...)
               and nothing above verifies it.

The route gets `attrs.webhook = {provider, verified, check, how, events}`; `cg routes` lists the check as a guard
(SECRET-CHECKED) or flags `WEBHOOK UNVERIFIED`. Event names compared with the event type (`switch (event.type)`,
`event["type"] == "x"`, `$event->type`, `data_get($event, 'type')`, the `X-GitHub-Event` header value) become
`endpoint:webhook:<provider>:<event>`, RECEIVED_BY the function holding the comparison and, when a branch calls at
most two project functions that are not shared by the other branches, by those too. @octokit/webhooks
`webhooks.on('push', fn)` receives `endpoint:webhook:github:push` directly.
"""
from __future__ import annotations

import re
from collections import defaultdict

from .brokers import JS_EXT, Scan as BrokerScan
from .core.model import EXACT, HEURISTIC
from .protocols import protocol_receive

# header (lower case) -> (provider, role); role sig / event / token / ts
HEADERS = {
    "stripe-signature": ("stripe", "sig"),
    "x-hub-signature-256": ("github", "sig"), "x-hub-signature": ("github", "sig"),
    "x-github-event": ("github", "event"), "x-github-delivery": ("github", "id"),
    "x-gitea-signature": ("gitea", "sig"), "x-gitea-event": ("gitea", "event"),
    "x-gitlab-token": ("gitlab", "token"), "x-gitlab-event": ("gitlab", "event"),
    "x-event-key": ("bitbucket", "event"),
    "x-slack-signature": ("slack", "sig"), "x-slack-request-timestamp": ("slack", "ts"),
    "x-shopify-hmac-sha256": ("shopify", "sig"), "x-shopify-topic": ("shopify", "event"),
    "x-twilio-signature": ("twilio", "sig"),
    "svix-signature": ("svix", "sig"), "webhook-signature": ("standard-webhooks", "sig"),
    "paddle-signature": ("paddle", "sig"), "x-razorpay-signature": ("razorpay", "sig"),
    "x-paystack-signature": ("paystack", "sig"), "x-square-hmacsha256-signature": ("square", "sig"),
    "x-signature-ed25519": ("discord", "sig"), "linear-signature": ("linear", "sig"),
    "x-cal-signature-256": ("cal.com", "sig"),
}
_HDR_ALT = "|".join(sorted((re.escape(h).replace("\\-", "[-_]") for h in HEADERS), key=len, reverse=True))
HDR_RX = re.compile(r"""['"](?:HTTP_)?(""" + _HDR_ALT + r""")(?=['".])""", re.I)
PY_HDR_RX = re.compile(r"\b(" + _HDR_ALT.replace("[-_]", "_") + r")\s*:[^=\n]*=\s*Header\s*\(", re.I)
# any other signature header: X-Partner-Signature, X-Webhook-Signature, X-Hmac-Sha256 ...
SIG_HDR_RX = re.compile(r"""['"](?:HTTP_)?(x[-_][\w-]*(?:signature|hmac)[\w-]*|signature)(?=['".])""", re.I)

VERIFY = [
    (re.compile(r"\bconstruct_?[Ee]vent(?:Async)?\s*\("), "stripe", "Stripe constructEvent", re.compile(r"(?i)stripe")),
    # `x.verify(..)`: the library is the one whose class built `x` (VERIFY_OBJ)
    (re.compile(r"\b(\w+)\s*\.\s*(?:verify|verifyAndReceive)\s*\("), None, None, re.compile(r"""@octokit/webhooks|svix|standardwebhooks|StandardWebhooks""")),
    (re.compile(r"\bvalidateRequest(?:WithBody)?\s*\(|\bRequestValidator\s*\([^)]*\)\s*\.\s*validate\s*\(|\bvalidator\s*\.\s*validate\s*\("),
     "twilio", "Twilio request validation", re.compile(r"(?i)twilio")),
    (re.compile(r"\bshopify\s*\.\s*webhooks\s*\.\s*validate\s*\("), "shopify", "Shopify webhooks.validate", None),
]
VERIFY_OBJ = [   # (constructor of the verifying object, import hint, provider, how)
    (r"Webhooks", re.compile(r"""['"]@octokit/webhooks['"]"""), "github", "@octokit/webhooks verify"),
    (r"Webhook", re.compile(r"""['"]svix['"]|\bfrom\s+svix\b|\bimport\s+svix\b|Svix\\"""), "svix", "svix Webhook.verify"),
    (r"Webhook", re.compile(r"""['"]standardwebhooks['"]|\bfrom\s+standardwebhooks\b|StandardWebhooks\\"""), "standard-webhooks",
     "standardwebhooks Webhook.verify"),
]
HMAC_RX = re.compile(r"\bcreateHmac\s*\(|\bhmac\s*\.\s*new\s*\(|\bhash_hmac\s*\(|\bHmac(?:Sha\d+)?\s*::\s*new_from_slice|"
                     r"\bMac\s*\.\s*getInstance\s*\(\s*\"Hmac|\bHMAC\s*<|\bHMAC\s*\(|\bhmac\s*\.\s*digest\s*\(")
CT_RX = re.compile(r"\btimingSafeEqual\s*\(|\bcompare_digest\s*\(|\bhash_equals\s*\(|\bconstant_time_compare\s*\(|"
                   r"\bMessageDigest\s*\.\s*isEqual\s*\(|\.\s*verify_slice\s*\(|\bconstant_time_eq\s*\(|\.\s*ct_eq\s*\(|"
                   r"\bsecureCompare\s*\(|\bsafeCompare\s*\(")
MW_RX = re.compile(r"(?i)verify\w*(?:webhook|signature)|webhook\w*(?:signature|verif)|stripe[-_.]webhook")
CODE_EXT = JS_EXT + (".jsx", ".py", ".php", ".kt", ".rs", ".swift", ".rb", ".java")
SHARED_CALLERS = 6
# helpers a branch calls that do not handle the event: predicates, readers, logging, responses
HELPER_NAME = re.compile(r"^(?:is|has|can|should|get|read|parse|to|format|log|audit|debug|info|warn|error|report|validate|"
                         r"verify|check|ensure|assert|json|response|abort)(?:[A-Z_]|$)")
# provider named by the route path or handler (`/mailgun_webhook`, `PaddleWebhookController`) when no header names it
PROVIDER_NAMES = re.compile(r"(?i)(stripe|github|gitlab|gitea|bitbucket|slack|shopify|twilio|svix|paddle|razorpay|paystack|"
                            r"square|discord|linear|mailgun|postmark|sendgrid|brevo|mollie|paypal|braintree|lemon_?squeezy|"
                            r"clerk|resend|calendly|zoom|telegram|whatsapp|intercom|hubspot|docusign|plaid|coinbase)")
EVENT_LIT = re.compile(r"[A-Za-z][\w.:/ -]{0,79}")
GENERIC_EVENT_VARS = {"event", "evt", "payload", "body", "data", "webhook", "notification", "webhookEvent",
                      "webhook_event", "stripeEvent", "stripe_event", "msg", "message"}
TYPE_KEYS = ("type", "event", "event_type", "eventType", "object_kind")


def _block(src, i, hi):
    """Offset after the `}` closing the `{` at `i` (strings and comments skipped); hi when unbalanced."""
    depth, j = 0, i
    while j < hi:
        c = src[j]
        if c in "'\"`":
            k = j + 1
            while k < hi and src[k] != c:
                k += 2 if src[k] == "\\" else 1
            j = k + 1
            continue
        if c == "/" and src.startswith("//", j):
            j = src.find("\n", j)
            j = hi if j < 0 else j
            continue
        if c == "/" and src.startswith("/*", j):
            j = src.find("*/", j)
            j = hi if j < 0 else j + 2
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return j + 1
        j += 1
    return hi


def _py_block(src, i, hi):
    """End of the indented block whose header line holds `i` (Python)."""
    ls = src.rfind("\n", 0, i) + 1
    ind = len(src[ls:i]) - len(src[ls:i].lstrip())
    j = src.find("\n", i)
    while 0 <= j < hi:
        nxt = src.find("\n", j + 1)
        line = src[j + 1:(nxt if nxt >= 0 else hi)]
        if line.strip() and len(line) - len(line.lstrip()) <= ind:
            return j + 1
        if nxt < 0:
            return hi
        j = nxt
    return hi


def _arms(src, lo, hi):
    """(arm start, `=>` offset, arm end) of the arms of a PHP match body src[lo:hi] (depth-0 `,` separated)."""
    out, depth, start, arrow, j = [], 0, lo, None, lo
    while j < hi:
        c = src[j]
        if c in "'\"":
            k = j + 1
            while k < hi and src[k] != c:
                k += 2 if src[k] == "\\" else 1
            j = k + 1
            continue
        if c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
        elif depth == 0 and src.startswith("=>", j) and arrow is None:
            arrow = j
        elif depth == 0 and c == "," and arrow is not None:
            out.append((start, arrow, j))
            start, arrow = j + 1, None
        j += 1
    if arrow is not None:
        out.append((start, arrow, hi))
    return out


class Webhooks(BrokerScan):
    def run(self) -> dict:
        from .tests_index import is_test_node
        self.is_test_node = is_test_node
        self.out = defaultdict(list)
        self.callees = defaultdict(list)          # fn -> [(dst, kind, line, confidence)]
        handlers = defaultdict(list)
        mw = defaultdict(list)
        callers = defaultdict(set)
        for e in self.b.edges.values():
            if e.kind in ("CALLS", "DISPATCHES"):
                self.callees[e.src].append((e.dst, e.kind, e.line, e.confidence))
                callers[e.dst].add(e.src)
            elif e.kind == "ROUTES_TO":
                handlers[e.src].append(e.dst)
            elif e.kind == "USES_MIDDLEWARE" and e.src.startswith("route:"):
                mw[e.src].append(e.dst)
        # shared helpers (logging, responses) called from many functions: their headers say nothing about the route
        self.shared = {d for d, c in callers.items() if len(c) > SHARED_CALLERS}
        for rid, n in list(self.b.nodes.items()):
            if n.kind != "route" or not handlers.get(rid):
                continue
            self.route(rid, n, handlers[rid], mw.get(rid, []))
        self.octokit_on()
        out = {k: v for k, v in self.st.items() if v}
        if out and self.samples:
            out["samples"] = dict(self.samples)
        return out

    # ------------------------------------------------------------ helpers
    def body(self, nid):
        n = self.b.nodes.get(nid)
        if n is None or n.kind not in ("function", "method") or not n.file or not n.line or not n.file.endswith(CODE_EXT):
            return None
        src = self.s.text(n.file)
        if not src:
            return None
        lo = self.s.off(n.file, n.line)
        end = n.end_line or n.line
        hi = self.s.off(n.file, end + 1) if end < len(self.s.lines[n.file]) else len(src)
        return n.file, src, lo, hi

    def tree(self, roots, depth=2):
        seen, order, frontier = set(), [], list(roots)
        for d in range(depth + 1):
            nxt = []
            for f in frontier:
                if f in seen or f not in self.b.nodes or self.is_test_node(self.b.nodes[f]):
                    continue
                seen.add(f)
                order.append(f)
                if d < depth:
                    nxt += [c[0] for c in self.callees.get(f, ())]
            frontier = nxt
        return order

    def finds(self, rx, file, src, lo, hi):
        for m in rx.finditer(src, lo, hi):
            if not self.s.masked(file, m.start()) or m.group(0)[:1] in "'\"":
                yield m

    def verify_obj(self, file, src, var):
        """(provider, how) when `var` was built by a webhook library's verifier class (`new Webhook(secret)`)."""
        if var in ("webhooks",) and re.search(r"""['"]@octokit/webhooks-methods['"]""", src):
            return "github", "@octokit/webhooks-methods verify"
        for cls, hint, prov, how in VERIFY_OBJ:
            if hint.search(src) and re.search(rf"\b(?:this\.|self\.|\$)?{re.escape(var)}\s*(?::\s*\w+\s*)?=\s*(?:new\s+)?(?:\w+\.)?{cls}\s*\(", src):
                return prov, how
        if var in ("webhooks", "Webhooks") and VERIFY_OBJ[0][1].search(src):
            return "github", "@octokit/webhooks verify"
        return None

    # ------------------------------------------------------------ receivers
    def route(self, rid, rn, hs, mws):
        fns = self.tree(list(hs) + list(mws))
        headers, sig_hdrs, verified = {}, [], None
        hm = ct = None
        bodies = {}
        for f in fns:
            bd = self.body(f)
            if bd is None:
                continue
            bodies[f] = bd
            file, src, lo, hi = bd
            own = f in hs or f in mws or f not in self.shared
            for m in (HDR_RX.finditer(src, lo, hi) if own else ()):
                h = m.group(1).lower().replace("_", "-")
                headers.setdefault(h, f"{file}:{self.s.line_of(file, m.start())}")
            if file.endswith(".py") and own:
                for m in PY_HDR_RX.finditer(src, lo, hi):
                    headers.setdefault(m.group(1).lower().replace("_", "-"), f"{file}:{self.s.line_of(file, m.start())}")
            for m in (SIG_HDR_RX.finditer(src, lo, hi) if own else ()):
                sig_hdrs.append(m.group(1).lower())
            for rx, prov, how, hint in VERIFY:
                if verified:
                    break
                if hint is not None and not hint.search(src):
                    continue
                for m in self.finds(rx, file, src, lo, hi):
                    p_h = (prov, how) if prov else self.verify_obj(file, src, m.group(1))
                    if p_h:
                        verified = (p_h[0], p_h[1], f"{file}:{self.s.line_of(file, m.start())}")
                        break
            if hm is None:
                hm = next((f"{file}:{self.s.line_of(file, m.start())}" for m in self.finds(HMAC_RX, file, src, lo, hi)), None)
            if ct is None:
                ct = next((f"{file}:{self.s.line_of(file, m.start())}" for m in self.finds(CT_RX, file, src, lo, hi)), None)
        provs = [HEADERS[h][0] for h in headers]
        provider = next((HEADERS[h][0] for h in headers if HEADERS[h][1] == "event"), provs[0] if provs else None)
        roles = {HEADERS[h][1] for h in headers}
        if verified is None:
            if ct and "token" in roles and "sig" not in roles:
                verified = (provider, "token header", ct)
            elif hm and (ct or "sig" in roles) and (provs or sig_hdrs):
                verified = (provider or "hmac", "HMAC signature" + ("" if ct else " (plain comparison)"), ct or hm)
            else:
                names = [x for x in (rn.attrs or {}).get("middleware") or [] if isinstance(x, str) and MW_RX.search(x)]
                if names and (provs or sig_hdrs or re.search(r"(?i)hook", rn.id)):
                    verified = (provider or "webhook", f"middleware {names[0]}", f"{rn.file}:{rn.line}")
        if verified is None and not provs:
            return
        prov = verified[0] if verified else provider
        if verified and provider and verified[0] in ("hmac", "webhook"):
            prov = provider
        if prov in (None, "hmac", "webhook"):
            nm = PROVIDER_NAMES.search(rn.id) or next((m for m in (PROVIDER_NAMES.search(h) for h in hs) if m), None)
            if nm:
                prov = nm.group(1).lower().replace("_", "")
        wh = {"provider": prov, "verified": bool(verified)}
        if verified:
            wh.update(how=verified[1], check=verified[2])
            if verified[1].startswith(("Stripe", "svix", "standardwebhooks")):
                wh["replay_protection"] = "timestamp tolerance (library)"
            if verified[1].endswith("(plain comparison)"):
                wh["constant_time"] = False
            self.st["webhook_receivers_verified"] += 1
        else:
            wh["headers"] = sorted(headers)
            self.st["webhook_receivers_unverified"] += 1
            self.miss("webhook_unverified", rid)
        rn.attrs["webhook"] = wh
        events = self.events(rid, prov, fns, bodies, headers, verified)
        if events:
            wh["events"] = sorted(events)

    def events(self, rid, prov, fns, bodies, headers, verified):
        got = set()
        guards = [f"webhook signature ({verified[1]})"] if verified else []
        for f in fns:
            if f not in bodies:
                continue
            file, src, lo, hi = bodies[f]
            py = file.endswith(".py")
            text = src[lo:hi]
            # event-type subjects: the verified event var's type, the event header value, generic payload names
            ev_vars = set(GENERIC_EVENT_VARS)
            strong = set()
            for m in re.finditer(r"(\$?\w+)\s*(?::\s*[\w<>\[\]]+\s*)?=\s*(?:await\s+)?[^;\n]*?(?:construct_?[Ee]vent(?:Async)?|\.verify)\s*\(", text):
                strong.add(m.group(1).lstrip("$"))
            ev_vars |= strong
            vv = "|".join(sorted(map(re.escape, ev_vars), key=len, reverse=True))
            keys = "|".join(TYPE_KEYS)
            type_expr = (rf"(?:\$(?:this->)?|this\.)?(?P<v>{vv})(?:\s*(?:\.|->|\?\.)\s*(?:{keys})\b(?!\s*\()"
                         rf"|\s*\[\s*['\"](?:{keys})['\"]\s*\]|\s*\.\s*get\s*\(\s*['\"](?:{keys})['\"]\s*\))"
                         rf"|data_get\s*\(\s*\$(?:this->)?(?P<v2>{vv})\s*,\s*['\"](?:{keys})['\"]\s*\)")
            hdr_expr = r"""[^;\n]*?['"](?:HTTP_)?(?:""" + "|".join(re.escape(h).replace("\\-", "[-_]") for h, (_p, r) in HEADERS.items() if r == "event") + r""")(?:\.\d+)?['"][^;\n]*"""
            subjects = [(type_expr, None)]
            # aliases: `$type = data_get($event, 'type')`, `name = req.headers['x-github-event']`
            alias = {}
            wrap = r"(?:(?:\$this->|self::|static::|Str::)?\w+\s*\(\s*(?:\((?:string|str)\)\s*)?)?"
            for m in re.finditer(rf"(\$?\w+)\s*(?::\s*\w+\s*)?=\s*{wrap}(?:{type_expr})", text):
                v = m.group("v") or m.group("v2")
                alias[m.group(1)] = EXACT if v in strong else HEURISTIC
            for m in re.finditer(rf"(?:const|let|var|\$|\b)(\$?\w+)\s*(?::\s*\w+\s*)?=\s*{hdr_expr}", text, re.I):
                alias[m.group(1).lstrip("$") if not m.group(0).startswith("$") else m.group(1)] = EXACT
            for a in alias:
                subjects.append((r"(?<![\w$.>])(?:\$|this\.|self\.)?" + re.escape(a.lstrip("$")) + r"\b", alias[a]))
            subjects.append((r"[\w.$>\-\[\]'\"]*\[\s*['\"](?:" + "|".join(re.escape(h) for h, (_p, r) in HEADERS.items() if r == "event") + r")['\"]\s*\]", EXACT))
            for subj, conf0 in subjects:
                for lit, pos, b_lo, b_hi, v in self.compares(file, src, lo, hi, subj, py):
                    if not EVENT_LIT.fullmatch(lit):
                        continue
                    conf = conf0 or (EXACT if (v in strong) else HEURISTIC)
                    if verified is None:
                        conf = HEURISTIC if conf == HEURISTIC else EXACT
                    self.recv(rid, prov, lit, f, file, pos, b_lo, b_hi, conf, guards)
                    got.add(lit)
            self.flush_branches(f)
        return got

    def compares(self, file, src, lo, hi, subj, py):
        """(literal, pos, branch lo, branch hi, var) for `switch (subj) { case 'x': }`, `match subj: case 'x':`,
        `subj === 'x'` / `'x' == subj`."""
        def q(n):
            return rf"""(?P<q{n}>['"])(?P<l{n}>(?:(?!(?P=q{n}))[^\\\n])+)(?P=q{n})"""

        def var(m):
            g = m.groupdict()
            return g.get("v") or g.get("v2")
        for m in re.finditer(rf"\bswitch\s*\(\s*(?:{subj})\s*\)\s*\{{", src[lo:hi]):
            s0 = lo + m.start()
            if self.s.masked(file, s0):
                continue
            body = lo + m.end()
            end = _block(src, body - 1, hi)
            cases, depth = [], 0
            for c in re.finditer(r"[{}]|\bcase\s+" + q(0) + r"\s*:|\bdefault\s*:", src[body:end]):
                t = c.group(0)
                if t == "{":
                    depth += 1
                elif t == "}":
                    depth -= 1
                elif depth == 0:
                    cases.append((body + c.start(), body + c.end(), c.group("l0") if t.startswith("case") else None))
            for i, (cs, ce, lit) in enumerate(cases):
                if lit is None:
                    continue
                nxt = i + 1
                while nxt < len(cases) and src[ce:cases[nxt][0]].strip() == "":
                    nxt += 1                                # fall-through labels share the next body
                b_hi = cases[nxt][0] if nxt < len(cases) else end
                yield lit, cs, ce, b_hi, var(m)
        if not py:
            # PHP 8 `match ($event) { 'push' => .., 'a', 'b' => .., default => .. }`
            for m in re.finditer(rf"\bmatch\s*\(\s*(?:{subj})\s*\)\s*\{{", src[lo:hi]):
                body = lo + m.end()
                if self.s.masked(file, body):
                    continue
                end = _block(src, body - 1, hi) - 1
                for a_lo, arrow, a_hi in _arms(src, body, end):
                    for lm in re.finditer(r"""(['"])((?:(?!\1)[^\\\n])+)\1""", src[a_lo:arrow]):
                        yield lm.group(2), a_lo + lm.start(), arrow, a_hi, var(m)
        if py:
            for m in re.finditer(rf"^[ \t]*match\s+(?:{subj})\s*:", src[lo:hi], re.M):
                s0 = lo + m.start()
                end = _py_block(src, s0, hi)
                for c in re.finditer(r"^[ \t]*case\s+" + q(0) + r"\s*:", src[s0:end], re.M):
                    p = s0 + c.start()
                    yield c.group("l0"), p, p, _py_block(src, p, end), var(m)
        for rx in (rf"(?:{subj})\s*(?P<op>===|==|!==|!=)\s*{q(1)}", rf"{q(1)}\s*(?P<op>===|==)\s*(?:{subj})"):
            for m in re.finditer(rx, src[lo:hi]):
                p0 = lo + m.start()
                if self.s.masked(file, p0):
                    continue
                lit, v = m.group("l1"), var(m)
                if "!" in m.group("op"):
                    yield lit, p0, lo + m.end(), hi, v
                    continue
                if py:
                    yield lit, p0, lo + m.end(), _py_block(src, p0, hi), v
                    continue
                e0 = lo + m.end()
                nl = src.find("\n", e0)
                ob = src.find("{", e0)
                if 0 <= ob < hi and (nl < 0 or ob <= nl):
                    yield lit, p0, e0, _block(src, ob, hi), v
                else:
                    yield lit, p0, e0, (nl if nl >= 0 else hi), v

    def recv(self, rid, prov, lit, fn, file, pos, b_lo, b_hi, conf, guards):
        line = self.s.line_of(file, pos)
        name = f"{prov or 'webhook'}:{lit}"
        key = (name, fn)
        if key not in self.done:
            self.done.add(key)
            protocol_receive(self.b, "webhook", name, fn, file, line, conf, guards=guards,
                             node_attrs={"provider": prov, "event": lit}, route=rid, how="event type")
            self.st["webhook_events"] += 1
        lo_l, hi_l = line, self.s.line_of(file, max(b_lo, b_hi - 1))
        self.out[fn].append((name, lo_l, hi_l, file, line, conf, guards, rid))

    def flush_branches(self, fn):
        """Thin branches: a branch calling at most two project functions not shared by other branches of the same
        dispatch also makes those functions receivers."""
        rows = self.out.pop(fn, [])
        if not rows:
            return
        calls = [(d, k, ln, c) for d, k, ln, c in self.callees.get(fn, ()) if ln and d in self.b.nodes and d not in self.shared
                 and self.b.nodes[d].kind in ("function", "method") and not self.is_test_node(self.b.nodes[d])
                 and not HELPER_NAME.match(re.split(r"[.:#]+", self.b.nodes[d].name or d)[-1])]
        per = []
        branches = {}                              # labels sharing one body (fall-through, `'a', 'b' =>`) count once
        for name, lo_l, hi_l, file, line, conf, guards, rid in rows:
            per.append({d for d, _k, ln, _c in calls if lo_l <= ln <= hi_l and d != fn})
            branches.setdefault((file, hi_l), set()).update(per[-1])
        shared = defaultdict(int)
        for ds in branches.values():
            for d in ds:
                shared[d] += 1
        for (name, lo_l, hi_l, file, line, conf, guards, rid), ds in zip(rows, per):
            if len(branches.get((file, hi_l)) or ()) > 2:
                continue                           # a branch doing several things inline: no single handler
            own = [d for d in ds if shared[d] == 1] if len(branches) > 1 else list(ds)
            if not 1 <= len(own) <= 2:
                continue
            for d in sorted(own):
                if (name, d) in self.done:
                    continue
                self.done.add((name, d))
                c = next((cc for dd, _k, ln, cc in calls if dd == d and lo_l <= ln <= hi_l), conf)
                protocol_receive(self.b, "webhook", name, d, file, line, HEURISTIC if HEURISTIC in (c, conf) else c,
                                 guards=guards, route=rid, how="event branch", via=fn)
                self.st["webhook_branch_handlers"] += 1

    # ------------------------------------------------------------ @octokit/webhooks
    def octokit_on(self):
        rx = re.compile(r"""\b(\w*[Ww]ebhooks)\s*\.\s*on\s*\(\s*(?:\[\s*)?(['"])([\w.]+)\2""")
        for f in sorted(self.s.files):
            if not f.endswith(JS_EXT) or f.endswith(".d.ts"):
                continue
            src = self.s.text(f)
            if "@octokit/webhooks" not in src and "probot" not in src:
                continue
            for m in rx.finditer(src):
                if self.s.masked(f, m.start()):
                    continue
                from .brokers import _args
                a = _args(src, src.find("(", m.end(1)))
                h = self.handler(f, m.start(), a[1] if len(a) > 1 else None)
                if not h:
                    continue
                if self.is_test(f, h):
                    continue
                protocol_receive(self.b, "webhook", f"github:{m.group(3)}", h, f, self.s.line_of(f, m.start()), EXACT,
                                 guards=["webhook signature (@octokit/webhooks)"], node_attrs={"provider": "github", "event": m.group(3)},
                                 how="@octokit/webhooks on")
                self.st["webhook_octokit_handlers"] += 1


def apply(project, builder, sock=None) -> dict:
    """Webhook receivers, their verification and the provider events they handle (#37 part 1)."""
    return Webhooks(project, builder, sock).run()
