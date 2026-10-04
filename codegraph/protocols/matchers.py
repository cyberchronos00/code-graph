"""Shared name matchers of the protocol registry (codegraph/protocols/__init__.py).

A matcher takes the name a sender uses and the name a receiver declares and returns None (no match) or a dict of
segment counts, used to rank candidates and to set the confidence of the MATCHES_ENDPOINT edge:

    lit          literal segment == literal segment
    wild         a receiver wildcard / {param} took one segment (`orders.*` / `orders.{id}` ~ `orders.42`)
    multi        a receiver multi-level wildcard took zero or more segments (MQTT `#`, NATS `>`, AMQP `#`, glob `**`)
    ph_into_lit  a sender placeholder (`orders.{id}` built from a template) fitted a receiver literal (heuristic)

Names use `{param}` for parameters on both sides (the builder normalises `${x}` / `:x` to it).
"""
from __future__ import annotations

import fnmatch
import re

PARAM = re.compile(r"^\{[^{}]*\}$")


def _info(**kw) -> dict:
    d = {"lit": 0, "wild": 0, "multi": 0, "ph_into_lit": 0}
    d.update(kw)
    return d


def exact(send: str, recv: str) -> dict | None:
    """Same name (case-sensitive)."""
    return _info(lit=1) if send == recv else None


def glob(send: str, recv: str) -> dict | None:
    """Receiver name as a shell-style glob (`*`, `?`, `[...]`) over the whole name; literal characters rank."""
    if send == recv:
        return _info(lit=1)
    if not any(c in recv for c in "*?["):
        return None
    if not fnmatch.fnmatchcase(send, recv):
        return None
    return _info(lit=len(re.sub(r"[*?]|\[[^\]]*\]", "", recv)), wild=1)


def path(send: str, recv: str) -> dict | None:
    """URL-style paths split on `/`: the HTTP route matcher of cg link (`{param}`, `{rest*}` catch-alls, embedded
    params), at least one literal segment in common."""
    from ..link import match_path
    ok, info = match_path(send, recv)
    if not ok or (info["lit"] == 0 and (send.strip("/") or recv.strip("/"))):
        return None
    return _info(lit=info["lit"], wild=info["param"] + info["lit_into_param"], ph_into_lit=info["ph_into_lit"])


def topic(sep: str, one: str | None, many: str | None, many_min: int = 0, many_last: bool = True):
    """Dotted / slashed topic names with single- and multi-level wildcards in the receiver's subscription:
    MQTT topic('/', '+', '#') (`#` last, also matches the parent level), NATS topic('.', '*', '>', many_min=1),
    AMQP topic exchange topic('.', '*', '#', many_last=False) (`#` anywhere, zero or more words).
    `{param}` segments act as single-level wildcards on the receiver side and as placeholders on the sender side."""

    def match(send: str, recv: str) -> dict | None:
        s, r = send.split(sep), recv.split(sep)
        if many_last and many in r[:-1]:
            return None                    # `#` / `>` only as the last level
        best: list[dict | None] = [None]

        def go(i: int, j: int, acc: dict):
            if j == len(r):
                if i == len(s):
                    rank = (acc["lit"], -acc["multi"], -acc["ph_into_lit"])
                    if best[0] is None or rank > (best[0]["lit"], -best[0]["multi"], -best[0]["ph_into_lit"]):
                        best[0] = dict(acc)
                return
            rs = r[j]
            if many is not None and rs == many:
                for k in range(i + many_min, len(s) + 1):
                    go(k, j + 1, dict(acc, multi=acc["multi"] + 1))
                return
            if i >= len(s):
                return
            ss = s[i]
            s_ph = bool(PARAM.match(ss))
            if (one is not None and rs == one) or PARAM.match(rs):
                go(i + 1, j + 1, dict(acc, wild=acc["wild"] + 1))
            elif ss == rs:
                go(i + 1, j + 1, dict(acc, lit=acc["lit"] + 1))
            elif s_ph:
                go(i + 1, j + 1, dict(acc, ph_into_lit=acc["ph_into_lit"] + 1))

        go(0, 0, _info())
        return best[0]

    match.__doc__ = f"topic matcher sep={sep!r} one={one!r} many={many!r}"
    return match


def _tpl_rx(name: str) -> re.Pattern:
    return re.compile("^" + "".join(".+?" if PARAM.match(part) else re.escape(part)
                                    for part in re.split(r"(\{[^{}]*\})", name)) + "$", re.S)


def template(send: str, recv: str) -> dict | None:
    """Whole-name `{param}` templates (`order:{id}` ~ `order:42`, Socket.IO `<namespace>#<event>`): a receiver
    template takes any non-empty text per parameter; a sender template fitting a receiver literal is a placeholder fit."""
    if send == recv:
        return _info(lit=1)
    if "{" in recv and _tpl_rx(recv).match(send):
        return _info(lit=len(re.sub(r"\{[^{}]*\}", "", recv)), wild=len(re.findall(r"\{[^{}]*\}", recv)))
    if "{" in send and "{" not in recv and _tpl_rx(send).match(recv):
        return _info(lit=len(re.sub(r"\{[^{}]*\}", "", send)), ph_into_lit=1)
    return None


def mcp(send: str, recv: str) -> dict | None:
    """MCP primitives `<server>/<name or uri template>`: a client that does not know the server sends `*/<name>`; the
    rest matches exactly or by `{param}` URI template (`notes://{id}` ~ `notes://42`)."""
    ss, _, sr = send.partition("/")
    rs, _, rr = recv.partition("/")
    if ss not in ("*", rs):
        return None
    i = template(sr, rr)
    if i is None:
        return None
    if ss == "*":
        i["wild"] += 1
    return i


mqtt = topic("/", "+", "#")
nats = topic(".", "*", ">", many_min=1)
amqp_topic = topic(".", "*", "#", many_last=False)
dotted = topic(".", None, None)


def amqp(send: str, recv: str) -> dict | None:
    """AMQP 0-9-1 `<exchange>/<routing key>` (exchange equal, key by topic-exchange rules: `*` one word, `#` zero or
    more) and `queue:<name>` (the default exchange: exact)."""
    if send.startswith("queue:") or recv.startswith("queue:"):
        return exact(send, recv)
    se, sl, sk = send.partition("/")
    re_, rl, rk = recv.partition("/")
    if not (sl and rl) or se != re_:
        return None
    i = amqp_topic(sk, rk)
    if i is not None:
        i["lit"] += 1
    return i          # `{param}` templates only (Laravel channel names, Socket.IO rooms)

MATCHERS = {"exact": exact, "glob": glob, "path": path, "template": template, "mqtt": mqtt, "nats": nats, "amqp_topic": amqp_topic,
            "dotted": dotted, "mcp": mcp, "amqp": amqp}


def rank(info: dict) -> tuple:
    """Higher is more specific: fewer placeholder-into-literal fits, more literal segments, fewer wildcards."""
    return (-info["ph_into_lit"], info["lit"], -info["multi"], -info["wild"])


def confidence(info: dict, tied: bool) -> str:
    if info["ph_into_lit"] or tied:
        return "heuristic"
    return "resolved" if (info["wild"] or info["multi"]) else "exact"


GENERIC_WEBHOOK = {"hmac", "webhook", "standard-webhooks", "svix"}


def webhook(send: str, recv: str) -> dict | None:
    """`<app>:<event>` (codegraph/webhooks.py): the same app and event; a receiver whose provider is only a generic
    signature scheme (`hmac`, standard webhooks, svix) takes any sending app's event of that name (heuristic, as a
    placeholder fit)."""
    if send == recv:
        return _info(lit=2)
    sa, _, se = send.partition(":")
    ra, _, re_ = recv.partition(":")
    if not se or se != re_ or PARAM.match(se):
        return None
    if ra in GENERIC_WEBHOOK:
        return _info(lit=1, ph_into_lit=1)
    return None


def extension(send: str, recv: str) -> dict | None:
    """Browser-extension messages (codegraph/local_ipc.py): message types by glob (a `*` listener takes every type);
    `port:<name>` connections only match `port:` listeners (runtime.connect never reaches runtime.onMessage)."""
    if send.startswith("port:") != recv.startswith("port:"):
        return None
    return glob(send, recv)
