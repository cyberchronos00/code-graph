"""Secret values never reach the graph database (#47).

A literal that belongs to a credential-looking key (password, secret, token, key, api_key, auth, private, credential,
passphrase, dsn, ...) is stored as `redacted:sha256:<first 8 hex of the digest>`: enough to see that a literal exists
and to tell two values apart, not enough to recover one. A password inside a URL (`scheme://user:pw@host`) gets the
same marker in place of the password so the host stays readable. Placeholders (`${X}`, `%(X)s`, `<x>`, `null`) and
values that are already markers are left alone. Applied at index time by the plugins that keep literal config values
(Laravel config/*.php `value` and `env()` defaults) and as a last pass over every node / edge attr before the write.
"""
from __future__ import annotations

import hashlib
import re

MARK = "redacted:sha256:"
SECRET_WORDS = ("PASSWORD", "PASS", "PASSWD", "SECRET", "TOKEN", "SECRET_KEY", "SECRET_ACCESS_KEY", "KEY", "AUTH", "PWD")
_EXTRA_WORDS = {"PASSWORD", "PASSWD", "PWD", "PASSPHRASE", "SECRET", "SECRETS", "TOKEN", "TOKENS", "AUTH", "AUTHORIZATION",
                "CREDENTIAL", "CREDENTIALS", "PRIVATE", "APIKEY", "DSN", "BEARER", "SALT", "SIGNATURE"}
_NOT_SECRET_TAIL = ("_KEY_ID", "_PUBLIC_KEY", "_KEY_PATH")
_URL_PW = re.compile(r"(?P<head>\b[A-Za-z][\w+.-]*://[^/\s:@'\"]*:)(?P<pw>[^@/\s'\"]+)(?P<tail>@)")
_PLACEHOLDER = ("${", "{{", "%(", "<", "$")


def secret_key(key: str) -> bool:
    """The `cg surface` rule for a config key / env name that holds a credential."""
    k = re.sub(r"[^A-Za-z0-9]+", "_", key).upper().strip("_")
    return any(k.endswith("_" + s) or k == s for s in SECRET_WORDS) and not k.endswith(_NOT_SECRET_TAIL)


def looks_secret(key: str) -> bool:
    """Wider than `secret_key`: any word of the name says credential (private_key_pem, db_dsn, apiKey, auth_header)."""
    if secret_key(key):
        return True
    spaced = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", str(key))
    words = [w for w in re.split(r"[^A-Za-z0-9]+", spaced.upper()) if w]
    if any(w in _EXTRA_WORDS for w in words):
        return True
    return "KEY" in words and not set(words) & {"PUBLIC", "ID", "PATH", "FILE", "NAME", "PREFIX", "TYPE", "SIZE", "LENGTH"}


def is_redacted(v) -> bool:
    return isinstance(v, str) and v.startswith(MARK)


def marker(value: str) -> str:
    return MARK + hashlib.sha256(value.encode("utf-8", "replace")).hexdigest()[:8]


def placeholder(v) -> bool:
    if not isinstance(v, str):
        return True
    t = v.strip()
    return not t or t.startswith(_PLACEHOLDER) or t.lower() in ("null", "none", "false", "true", "changeme?") or is_redacted(t)


def redact_url(v: str) -> str:
    if "://" not in v or "@" not in v:
        return v
    return _URL_PW.sub(lambda m: m["head"] + (m["pw"] if placeholder(m["pw"]) else marker(m["pw"])) + m["tail"], v)


def redact(value, key: str | None = None):
    """`value` as it may be stored: a marker for a literal under a credential-looking `key`, a URL password replaced
    by one, anything else unchanged. Non-strings and placeholders pass through."""
    if not isinstance(value, str):
        return value
    if key is not None and looks_secret(key) and not placeholder(value):
        return marker(value)
    return redact_url(value)


_VALUE_KEYS = {"value", "literal", "default", "env_default", "default_value"}
_NAME_KEYS = ("key", "name", "env", "env_key", "setting")
# Names that only ever carry a literal credential. `token`, `secret`, `credential(s)` are left out on purpose: plugins
# use them for identifiers (the Nest dependency-injection token on INJECTS edges, a secret's name, a credential source).
_DIRECT = {"password", "passwd", "pwd", "passphrase", "api_key", "apikey", "access_key", "secret_key", "client_secret",
           "private_key", "auth_token"}
_REFERENCE = re.compile(r"^(?:env|config|file|const|literal)[: ]|^\$\{|^[\w./-]+:\d+$")
STRUCTURAL_EDGES = {"INJECTS", "BOUND_TO", "CREDENTIAL_FROM"}
# Attr maps keyed by source identifiers: an exported class called `ApiKey` is a name, not a literal under a credential key.
_IDENTIFIER_MAPS = {"reexports"}


def _holds_secret(d: dict, k: str) -> bool:
    if k.lower() in _DIRECT:
        return not _REFERENCE.match(str(d[k]))
    if k in _VALUE_KEYS:
        return any(isinstance(d.get(nk), str) and looks_secret(d[nk]) for nk in _NAME_KEYS)
    return False


def sweep(builder) -> int:
    """Last pass over node / edge attrs; returns the count of replaced values. Replaces the password of a
    `scheme://user:password@host` string, a literal under a credential-named attr (`password`, `token`, ...), and the
    `value` / `default` of a `{key|name: <credential-looking>, value: ...}` record (TypeScript / Python config readers,
    Laravel config). Booleans such as `credential_literal`, hosts and every other value stay as they are."""
    n = 0

    def walk(x, name=None):
        nonlocal n
        if name in _IDENTIFIER_MAPS:
            return x
        if isinstance(x, str):
            if "@" in x and "://" in x:
                y = redact_url(x)
                if y != x:
                    n += 1
                return y
            return x
        if isinstance(x, dict):
            for k, v in x.items():
                if isinstance(v, str) and _holds_secret(x, k) and not placeholder(v):
                    x[k] = marker(v)
                    n += 1
                    continue
                nv = walk(v, k)
                if nv is not v:
                    x[k] = nv
            return x
        if isinstance(x, list):
            for i, v in enumerate(x):
                nv = walk(v)
                if nv is not v:
                    x[i] = nv
            return x
        return x
    for node in builder.nodes.values():
        doc = getattr(node, "doc", None)
        if isinstance(doc, str) and "@" in doc and "://" in doc:
            y = redact_url(doc)
            if y != doc:
                node.doc = y
                n += 1
        if node.attrs:
            walk(node.attrs)
    for e in builder.edges.values():
        if e.attrs and e.kind not in STRUCTURAL_EDGES:
            walk(e.attrs)
    return n
