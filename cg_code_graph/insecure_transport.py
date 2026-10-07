"""Disabled TLS / SSH verification, plaintext gRPC channels and IPC exposure facts (#47 part 2a).

An index-time regex pass, run next to the external / SDK passes. Each hit is appended to the list attr
`insecure_transport` of the innermost function / method that contains it (the module / file node at module level, a
`file:config:<path>` node for manifests, scripts and config files) as

    {kind, line, lib, detail, confidence}      (+ `system` when the call resolved to a CONNECTS_TO / HTTP_CALLS edge)

kinds:
  tls-verify-off      requests / httpx / aiohttp / ssl, node tls / https / axios / got, curl / Guzzle / Laravel Http,
                      Go, Java / Kotlin trust-all managers, Rust reqwest, Dart; `NODE_TLS_REJECT_UNAUTHORIZED=0`
                      (process-wide) in code, `.env.example`, compose, Dockerfile, package.json scripts, CI files
  ssh-hostkey-off     paramiko policies, `StrictHostKeyChecking=no` / `UserKnownHostsFile=/dev/null`, Go, ssh2, ansible
  grpc-plaintext      insecure gRPC channels and ports (`unix:` and loopback targets are not hits, only counted)
  ipc-extension-manifest   manifest.json `externally_connectable` with wildcard matches / ids
  ipc-extension-external   `runtime.onMessageExternal` / `onConnectExternal` listeners with no sender check
  ipc-electron        `webPreferences` with nodeIntegration / contextIsolation / webSecurity switched the wrong way

When the hit is inside a call that is already a CONNECTS_TO edge (`cg external`) or an HTTP_CALLS edge (`cg api-calls`),
that edge and its external system also get `tls_verify: false`. Test files and test callers are skipped and counted
(`tests_skipped`). `.env` files are never read: only `.env.example` style files, compose files, Dockerfiles,
package.json scripts, CI files, shell scripts, ssh_config and ansible.cfg. `cg surface` reads these facts and never
rescans source.
"""
from __future__ import annotations

import json
import os
import re
from collections import defaultdict

from .presets import skip_dirs
from .tests_index import is_test_node

SKIP = skip_dirs("common") | {
    "vendor", "target", "build", "dist", ".dart_tool", ".venv", "venv", ".tox", ".mypy_cache", ".pytest_cache",
    ".next", ".output", "out", ".gradle", ".idea", "Pods", ".build", ".swiftpm", "DerivedData", "Carthage", "coverage",
    "node_modules",
}
CODE = {
    ".py": "py", ".php": "php", ".go": "go", ".rs": "rs", ".dart": "dart", ".kt": "kt", ".kts": "kt", ".java": "java",
    **{e: "js" for e in (".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".mts", ".cts", ".vue", ".svelte")},
}
SCRIPT_EXT = {".sh", ".bash", ".zsh", ".ksh", ".mk"}
YAML_EXT = {".yml", ".yaml"}
ENV_EXAMPLE = re.compile(r"(?:^|[./_-])env\.(?:example|sample|dist|template)$|^\.env\.(?:example|sample|dist|template)$", re.I)
TEST_PATH = re.compile(
    r"(?:^|/)(?:tests?|__tests__|spec|specs|e2e|cypress|__mocks__|testing)/|(?:^|/)test_[^/]*\.py$|_test\.(?:py|go|rs|dart)$|"
    r"\.(?:test|spec)\.[cm]?[jt]sx?$|(?:Test|Tests|Spec)\.(?:php|java|kt)$|(?:^|/)conftest\.py$", re.I)
COMMENT = ("#", "//", "/*", "*", "--", "<!--", ";")
MAX_BYTES = 1_500_000
LOOPBACK = {"localhost", "127.0.0.1", "::1", "0.0.0.0", "[::1]", "host.docker.internal", ""}

I = re.I
S = re.S

# (kind, groups, regex, lib, detail, confidence); `groups` are the CODE groups or the config groups below
CONFIG_GROUPS = ("script", "docker", "yaml", "pkgjson", "envex", "sshcfg", "ansible", "ci")
ANY_TEXT = tuple(set(CODE.values())) + CONFIG_GROUPS
NODE_TLS = (r"""NODE_TLS_REJECT_UNAUTHORIZED["']?\]?\s*[=:\s]\s*["']?0\b(?!\s*=)""")
RULES: list[tuple] = [
    # ---- TLS verification
    ("tls-verify-off", ("py",), re.compile(r"\bverify\s*=\s*False\b"), "@pylib", "certificate verification is switched off (verify=False)", "@pyconf"),
    ("tls-verify-off", ("py",), re.compile(r"\bverify_ssl\s*=\s*False\b"), "@pylib", "certificate verification is switched off (verify_ssl=False)", "heuristic"),
    ("tls-verify-off", ("py",), re.compile(r"\bssl\s*=\s*False\b"), "aiohttp", "certificate verification is switched off (ssl=False)", "@aiohttp"),
    ("tls-verify-off", ("py",), re.compile(r"\bssl\._create_unverified_context\b|\b_create_unverified_context\s*\("), "ssl", "ssl._create_unverified_context skips certificate checks", "exact"),
    ("tls-verify-off", ("py",), re.compile(r"\bCERT_NONE\b"), "ssl", "ssl.CERT_NONE accepts any certificate", "exact"),
    ("tls-verify-off", ("py",), re.compile(r"\bcheck_hostname\s*=\s*False\b"), "ssl", "check_hostname is switched off", "exact"),
    ("tls-verify-off", ("js",), re.compile(r"\brejectUnauthorized\s*:\s*false\b"), "@jslib", "rejectUnauthorized: false accepts any certificate", "exact"),
    ("tls-verify-off", ("js",), re.compile(r"\bstrictSSL\s*:\s*false\b"), "request", "strictSSL: false accepts any certificate", "exact"),
    ("tls-verify-off", ("js",), re.compile(r"\bprocess\s*\.\s*env\s*(?:\.\s*|\[\s*['\"])" + NODE_TLS), "node", "NODE_TLS_REJECT_UNAUTHORIZED=0 in code: certificate checks are off for the whole process", "exact"),
    ("tls-verify-off", ("php",), re.compile(r"\bCURLOPT_SSL_VERIFYPEER\b\s*(?:=>|,)\s*(?:false|0)\b", I), "curl", "CURLOPT_SSL_VERIFYPEER is false", "exact"),
    ("tls-verify-off", ("php",), re.compile(r"\bCURLOPT_SSL_VERIFYHOST\b\s*(?:=>|,)\s*(?:false|0)\b", I), "curl", "CURLOPT_SSL_VERIFYHOST is 0", "exact"),
    ("tls-verify-off", ("php",), re.compile(r"""['"]verify['"]\s*=>\s*false\b""", I), "@phplib", "'verify' => false skips certificate checks", "@phpconf"),
    ("tls-verify-off", ("php",), re.compile(r"\bHttp\s*::\s*withoutVerifying\s*\(|->\s*withoutVerifying\s*\("), "laravel-http", "withoutVerifying() skips certificate checks", "exact"),
    ("tls-verify-off", ("php",), re.compile(r"""['"]verify_peer(?:_name)?['"]\s*=>\s*false\b""", I), "stream-context", "stream context verify_peer is false", "exact"),
    ("tls-verify-off", ("go",), re.compile(r"\bInsecureSkipVerify\s*[:=]\s*true\b"), "crypto/tls", "InsecureSkipVerify: true accepts any certificate", "exact"),
    ("tls-verify-off", ("rs",), re.compile(r"\bdanger_accept_invalid_(?:certs|hostnames)\s*\(\s*true\s*\)"), "reqwest", "danger_accept_invalid_certs / hostnames accepts any certificate", "exact"),
    ("tls-verify-off", ("kt", "java"), re.compile(r"\bcheckServerTrusted\s*\([^)]*\)\s*(?:throws\s+[\w.,\s]+)?\s*(?::\s*Unit\s*)?\{\s*(?://[^\n]*\n\s*|/\*.*?\*/\s*)*\}", S), "javax.net.ssl", "trust-all X509TrustManager (empty checkServerTrusted)", "exact"),
    ("tls-verify-off", ("kt", "java"), re.compile(
        r"\bverify\s*\([^)]*\)\s*(?::\s*Boolean\s*)?(?:=\s*true\b|\{\s*return\s+true\s*;?\s*\})|"
        r"\bhostnameVerifier\s*(?:\(\s*)?\{\s*_?\w*\s*,\s*_?\w*\s*->\s*true\s*\}|"
        r"\b(?:setHostnameVerifier|hostnameVerifier)\s*\(\s*\(\s*\w+\s*,\s*\w+\s*\)\s*->\s*true\s*\)|\bALLOW_ALL_HOSTNAME_VERIFIER\b", S),
     "okhttp", "HostnameVerifier that always returns true", "exact"),
    ("tls-verify-off", ("dart",), re.compile(r"\bbadCertificateCallback\s*=\s*\([^)]*\)\s*=>\s*true\b"), "dart:io", "badCertificateCallback accepts any certificate", "exact"),
    ("tls-verify-off", ("script", "docker", "yaml", "pkgjson", "envex", "ci"), re.compile(NODE_TLS), "node", "NODE_TLS_REJECT_UNAUTHORIZED=0: certificate checks are off for the whole process", "exact"),
    # ---- SSH host keys
    ("ssh-hostkey-off", ("py",), re.compile(r"\bset_missing_host_key_policy\s*\(\s*[\w.]*?(?:AutoAddPolicy|WarningPolicy)\b"), "paramiko", "SSH host key not checked (AutoAddPolicy / WarningPolicy)", "exact"),
    ("ssh-hostkey-off", ("py",), re.compile(r"\bknown_hosts\s*=\s*None\b"), "asyncssh", "SSH host key not checked (known_hosts=None)", "@asyncssh"),
    ("ssh-hostkey-off", ("go",), re.compile(r"\bssh\s*\.\s*InsecureIgnoreHostKey\s*\("), "x/crypto/ssh", "SSH host key not checked (InsecureIgnoreHostKey)", "exact"),
    ("ssh-hostkey-off", ("js",), re.compile(r"\bhostVerifier\s*:\s*(?:\([^)]*\)|\w+)\s*=>\s*true\b|\bhostVerifier\s*:\s*function\s*\([^)]*\)\s*\{\s*return\s+true\s*;?\s*\}"), "ssh2", "SSH host key not checked (hostVerifier returns true)", "exact"),
    ("ssh-hostkey-off", ANY_TEXT, re.compile(r"""\bStrictHostKeyChecking["']?\s*[=:,\s]\s*["']?(?:no|off|false)\b""", I), "ssh", "SSH host key not checked (StrictHostKeyChecking=no)", "exact"),
    ("ssh-hostkey-off", ANY_TEXT, re.compile(r"""\bUserKnownHostsFile["']?\s*[=:,\s]\s*["']?/dev/null\b""", I), "ssh", "SSH host key not remembered (UserKnownHostsFile=/dev/null)", "exact"),
    ("ssh-hostkey-off", ("ansible",), re.compile(r"^\s*host_key_checking\s*=\s*(?:False|false|no|0)\b", re.M), "ansible", "SSH host key not checked (host_key_checking = False)", "heuristic"),
    ("ssh-hostkey-off", ("script", "docker", "yaml", "envex", "ci", "py", "js"), re.compile(r"""\bANSIBLE_HOST_KEY_CHECKING["']?\s*[=:\s]\s*["']?(?:False|false|no|0)\b"""), "ansible", "SSH host key not checked (ANSIBLE_HOST_KEY_CHECKING=False)", "heuristic"),
    # ---- gRPC without TLS (the target group `t` is read for the unix: / loopback exclusion)
    ("grpc-plaintext", ("py",), re.compile(r"\bgrpc\s*\.\s*(?:aio\s*\.\s*)?insecure_channel\s*\(\s*(?P<t>[^,)]*)"), "grpc", "client channel without TLS", "@t"),
    ("grpc-plaintext", ("py",), re.compile(r"\.\s*add_insecure_port\s*\(\s*(?P<t>[^,)]*)"), "grpc", "server port without TLS", "@t"),
    ("grpc-plaintext", ("js",), re.compile(r"\b(?:Server)?[Cc]redentials\s*\.\s*createInsecure\s*\("), "@grpc-js", "channel / server credentials without TLS (createInsecure)", "@back"),
    ("grpc-plaintext", ("java", "kt"), re.compile(r"\.\s*usePlaintext\s*\("), "grpc-java", "channel without TLS (usePlaintext)", "@back"),
    ("grpc-plaintext", ("go",), re.compile(r"\bgrpc\s*\.\s*WithInsecure\s*\(|\binsecure\s*\.\s*NewCredentials\s*\("), "grpc-go", "channel without TLS (WithInsecure / insecure.NewCredentials)", "@back"),
    ("grpc-plaintext", ("rs",), re.compile(r"""(?:\b(?:Channel|Endpoint)\s*::\s*(?:from_static|from_shared|new)|Client\s*::\s*connect)\s*\(\s*(?P<t>"http://[^"]*")"""), "tonic", "channel without TLS (http:// target)", "@t"),
    ("grpc-plaintext", ("dart",), re.compile(r"\bChannelCredentials\s*\.\s*insecure\s*\("), "grpc-dart", "channel without TLS (ChannelCredentials.insecure)", "@back"),
]
_TLS_ENV_LIBS = ("axios", "got", "undici", "node-fetch", "superagent", "request", "https", "tls", "ws", "mqtt", "nodemailer", "pg")
_BACK_TARGET = {
    "js": re.compile(r"(?P<t>[^,()]+?)\s*,\s*$"),
    "java": re.compile(r"for(?:Address|Target)\s*\((?P<t>[^)]*)\)(?:(?!;)[\s\S])*$"),
    "kt": re.compile(r"for(?:Address|Target)\s*\((?P<t>[^)]*)\)(?:(?!\n\s*\n)[\s\S])*$"),
    "go": re.compile(r"\b(?:Dial|NewClient)\w*\s*\(\s*(?:[\w.]+\s*,\s*)?(?P<t>[^,()]+)(?:(?!\bDial|\bNewClient)[\s\S])*$"),
    "dart": re.compile(r"ClientChannel\s*\(\s*(?P<t>[^,()]+)(?:[\s\S])*$"),
}


def _back_target(text: str, pos: int, group: str) -> str | None:
    """The target argument before a credentials / plaintext marker in the same call, when it is spelled out."""
    rx = _BACK_TARGET.get(group)
    if rx is None:
        return None
    win = text[max(0, pos - 400):pos]
    if group == "js":
        win = re.sub(r"(?:[\w$]+\s*\.\s*)*$", "", win[-240:]).rstrip()
    m = rx.search(win)
    return m.group("t") if m else None


def _first_arg(raw: str | None) -> str | None:
    if raw is None:
        return None
    depth, quote = 0, None
    for i, c in enumerate(raw):
        if quote:
            quote = None if c == quote else quote
        elif c in "\"'`":
            quote = c
        elif c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
        elif c == "," and depth == 0:
            return raw[:i]
    return raw


def _config_group(rel: str) -> str | None:
    base = rel.rsplit("/", 1)[-1]
    low = base.lower()
    if low == ".env" or (low.startswith(".env") and not ENV_EXAMPLE.search(base)) or low.endswith(".env"):
        return None
    if ENV_EXAMPLE.search(base):
        return "envex"
    if low == "ansible.cfg":
        return "ansible"
    if low in ("ssh_config", "sshd_config") or rel.endswith(".ssh/config"):
        return "sshcfg"
    if low.startswith("dockerfile") or low.endswith(".dockerfile") or low == "containerfile":
        return "docker"
    if low == "package.json":
        return "pkgjson"
    if low in ("makefile", "gnumakefile") or os.path.splitext(low)[1] in SCRIPT_EXT:
        return "script"
    if low in ("jenkinsfile", ".gitlab-ci.yml", "azure-pipelines.yml", "bitbucket-pipelines.yml", ".travis.yml") \
            or rel.startswith((".github/", ".circleci/")):
        return "ci"
    if os.path.splitext(low)[1] in YAML_EXT:
        return "yaml"
    return None


def _is_comment(text: str, pos: int, group: str) -> bool:
    ls = text.rfind("\n", 0, pos) + 1
    head = text[ls:pos].lstrip()
    if group in ("pkgjson",):
        return False
    return head.startswith(COMMENT) and not (group in ("php",) and head.startswith("#["))


def _line_of(text: str, pos: int) -> int:
    return text.count("\n", 0, pos) + 1


def _call_end(text: str, open_at: int, limit: int = 4000) -> int:
    depth = 0
    for i in range(open_at, min(len(text), open_at + limit)):
        c = text[i]
        if c in "({[":
            depth += 1
        elif c in ")}]":
            depth -= 1
            if depth == 0:
                return i + 1
    return min(len(text), open_at + limit)


def _lit(expr: str) -> str | None:
    e = (expr or "").strip()
    m = re.fullmatch(r"""[fF]?(["'`])(.*)\1""", e, re.S)
    return m.group(2) if m else None


def target_kind(raw: str | None, server: bool = False) -> tuple[str, str | None]:
    """('unix' | 'loopback' | 'remote' | 'unknown', literal host:port or None) of a gRPC target expression."""
    if raw is None:
        return "unknown", None
    lit = _lit(raw)
    if lit is None:
        bare = raw.strip().strip("{}")
        if re.fullmatch(r"[A-Za-z_][\w.]*", bare) and bare.lower() in ("localhost",):
            lit = bare
        elif re.fullmatch(r"[\d.]+|\[?[0-9a-fA-F:]+\]?", bare) and bare:
            lit = bare
        else:
            return "unknown", None
    t = lit.strip()
    if t.startswith(("unix:", "unix-abstract:")):
        return "unix", t
    t = re.sub(r"^(?:dns|ipv4|ipv6):/{0,3}|^https?://", "", t)
    host = t.rsplit(":", 1)[0] if re.search(r":\d+$", t) else t
    host = host.strip("[]/").lower()
    if host in ("localhost", "::1", "ip6-localhost") or host.startswith("127.") or host.endswith(".localhost") \
            or (host == "0.0.0.0" and not server):
        return "loopback", t
    return "remote", t


def _is_test_path(rel: str) -> bool:
    return bool(TEST_PATH.search(rel))


class _Owners:
    """Innermost function / method of a file's line, the file's module node otherwise, a file:config node as last resort."""

    def __init__(self, builder):
        self.b = builder
        self.fns: dict[str, list] = defaultdict(list)
        self.mods: dict[str, str] = {}
        for n in builder.nodes.values():
            f = (n.file or "").replace("\\", "/")
            if not f:
                continue
            if n.kind in ("function", "method", "constructor") and n.line:
                self.fns[f].append((n.line, n.end_line or n.line, n.id))
            elif n.kind in ("module", "script", "file") and f not in self.mods:
                self.mods[f] = n.id

    def of(self, rel: str, line: int) -> str:
        best = None
        for lo, hi, nid in self.fns.get(rel, ()):
            if lo <= line <= hi and (best is None or hi - lo < best[0]):
                best = (hi - lo, nid)
        if best:
            return best[1]
        if rel in self.mods:
            return self.mods[rel]
        return self.b.add_node("file", f"config:{rel}", name=rel, file=rel, line=1, lang="config")


def _py_lib(text: str) -> str | None:
    for lib in ("requests", "httpx", "aiohttp", "urllib3"):
        if re.search(rf"^\s*(?:import|from)\s+{lib}\b", text, re.M):
            return lib
    return None


def _js_lib(text: str) -> str:
    for lib in _TLS_ENV_LIBS:
        if re.search(rf"""['"](?:node:)?{re.escape(lib)}['"]""", text):
            return lib
    return "node-tls"


def _php_lib(text: str) -> str | None:
    if re.search(r"GuzzleHttp|\bHttp\s*::|new\s+Client\b", text):
        return "guzzle" if "GuzzleHttp" in text else "laravel-http"
    return None


def scan_text(text: str, rel: str, group: str) -> list[dict]:
    """Facts in one file's text (no graph access): [{kind, line, lib, detail, confidence, pos}]."""
    out: list[dict] = []
    seen: set = set()
    py_lib = _py_lib(text) if group == "py" else None
    php_lib = _php_lib(text) if group == "php" else None
    js_lib = _js_lib(text) if group == "js" else None
    has_tonic = group == "rs" and "tonic" in text
    has_asyncssh = group == "py" and "asyncssh" in text
    for kind, groups, rx, lib, detail, conf in RULES:
        if group not in groups:
            continue
        if kind == "grpc-plaintext" and group == "rs" and not has_tonic:
            continue
        for m in rx.finditer(text):
            if _is_comment(text, m.start(), group):
                continue
            line = _line_of(text, m.start())
            if (kind, line) in seen:
                continue
            c, lb, dt = conf, lib, detail
            if lb == "@pylib":
                lb = py_lib or "python-http"
                c = "resolved" if py_lib else "heuristic" if conf == "@pyconf" else conf
            elif lb == "@jslib":
                lb = js_lib
            elif lb == "@phplib":
                lb = php_lib or "php-http"
            if conf == "@pyconf":
                c = "resolved" if py_lib else "heuristic"
            elif conf == "@phpconf":
                c = "resolved" if php_lib else "heuristic"
            elif conf == "@aiohttp":
                if "aiohttp" not in text:
                    continue
                c = "resolved"
            elif conf == "@asyncssh":
                if not has_asyncssh:
                    continue
                c = "resolved"
            elif conf in ("@t", "@back"):
                server = bool(re.search(r"Server|add_insecure_port", m.group(0)))
                raw = m.groupdict().get("t") if conf == "@t" else _back_target(text, m.start(), group)
                tk, tgt = target_kind(_first_arg(raw), server=server)
                if tk in ("unix", "loopback"):
                    out.append({"kind": "grpc-local", "line": line, "lib": lb, "detail": tk, "confidence": "exact", "pos": m.start()})
                    seen.add((kind, line))
                    continue
                c = "resolved" if tk == "remote" else "heuristic"
                dt = f"{detail}: {tgt}" if tgt else f"{detail} (target not a literal)"
            out.append({"kind": kind, "line": line, "lib": lb, "detail": dt, "confidence": c, "pos": m.start()})
            seen.add((kind, line))
    return out


# ---------------------------------------------------------------- IPC: manifest, extension listeners, Electron
WILD_MATCH = re.compile(r"^(?:<all_urls>|\*://\*/\*|\*://\*\.\*/\*|https?://\*/\*|\*://\*/?|\*)$")
EXT_EXTERNAL = re.compile(r"\b(?:chrome|browser)\s*\.\s*runtime\s*\.\s*(onMessageExternal|onConnectExternal)\s*\.\s*addListener\s*\(")
SENDER_CHECK = re.compile(r"\bsender\s*(?:\??\.\s*(?:id|origin|url|tab|frameId)\b|\[\s*['\"](?:id|origin|url)['\"])|\bport\s*\.\s*sender\b|\bsender\b[^;\n]*\b(?:id|origin|url)\b")
WEBPREF = re.compile(r"\bwebPreferences\s*:\s*\{")
ELECTRON_KEYS = (
    ("nodeIntegration", re.compile(r"\bnodeIntegration\s*:\s*true\b"), "webPreferences.nodeIntegration is true: page content can call Node APIs"),
    ("contextIsolation", re.compile(r"\bcontextIsolation\s*:\s*false\b"), "webPreferences.contextIsolation is false: page scripts share the preload context"),
    ("webSecurity", re.compile(r"\bwebSecurity\s*:\s*false\b"), "webPreferences.webSecurity is false: same-origin checks are off"),
)


def manifest_facts(text: str) -> list[dict]:
    if '"externally_connectable"' not in text:
        return []
    try:
        d = json.loads(text)
    except ValueError:
        try:
            d = json.loads(re.sub(r"^\s*//.*$", "", text, flags=re.M))
        except ValueError:
            return []
    if not isinstance(d, dict) or not isinstance(d.get("externally_connectable"), dict):
        return []
    ec = d["externally_connectable"]
    lines = text.splitlines()
    line = next((i + 1 for i, ln in enumerate(lines) if '"externally_connectable"' in ln), 1)

    def at(key: str) -> int:
        return next((i + 1 for i, ln in enumerate(lines) if i + 1 >= line and f'"{key}"' in ln), line)
    out = []
    wild = [m for m in ec.get("matches") or [] if isinstance(m, str) and WILD_MATCH.match(m.strip())]
    if wild:
        out.append({"kind": "ipc-extension-manifest", "line": at("matches"), "lib": "webextension", "confidence": "exact",
                    "detail": f"externally_connectable.matches accepts any web page ({wild[0]})"})
    if "*" in (ec.get("ids") or []):
        out.append({"kind": "ipc-extension-manifest", "line": at("ids"), "lib": "webextension", "confidence": "exact",
                    "detail": "externally_connectable.ids accepts any extension (\"*\")"})
    return out


def _callback_body(text: str, open_at: int) -> str:
    end = _call_end(text, open_at)
    args = text[open_at:end]
    m = re.match(r"\(\s*([A-Za-z_$][\w$]*)\s*[,)]", args)
    if m and not re.match(r"\(\s*(?:async\s+)?(?:function|\(|[\w$]+\s*=>)", args):
        name = m.group(1)
        d = re.search(rf"(?:function\s+{re.escape(name)}\s*\([^)]*\)\s*\{{|(?:const|let|var)\s+{re.escape(name)}\s*=\s*(?:async\s*)?(?:function\s*)?\([^)]*\)\s*(?:=>)?\s*\{{)", text)
        if d:
            return text[d.start():_call_end(text, d.end() - 1)]
    return args


def js_ipc_facts(text: str) -> list[dict]:
    out = []
    if "chrome." in text or "browser." in text:
        for m in EXT_EXTERNAL.finditer(text):
            if _is_comment(text, m.start(), "js"):
                continue
            body = _callback_body(text, m.end() - 1)
            if SENDER_CHECK.search(body):
                continue
            which = m.group(1)
            out.append({"kind": "ipc-extension-external", "line": _line_of(text, m.start()), "lib": "webextension",
                        "confidence": "resolved", "pos": m.start(),
                        "detail": f"runtime.{which} listener never checks sender.id / sender.origin / sender.url"})
    if "electron" in text.lower() or "BrowserWindow" in text:
        for m in WEBPREF.finditer(text):
            if _is_comment(text, m.start(), "js"):
                continue
            end = _call_end(text, m.end() - 1)
            body = text[m.end() - 1:end]
            for key, rx, detail in ELECTRON_KEYS:
                k = rx.search(body)
                if k:
                    out.append({"kind": "ipc-electron", "line": _line_of(text, m.end() - 1 + k.start()), "lib": "electron",
                                "confidence": "exact", "detail": detail, "pos": m.end() - 1 + k.start()})
    return out


# ---------------------------------------------------------------- the pass
def _files(root) -> list[tuple[str, str]]:
    out = []
    for d, dirs, files in os.walk(root):
        dirs[:] = sorted(x for x in dirs if x not in SKIP and not (x.startswith(".") and x not in (".github", ".circleci", ".ssh")))
        for f in sorted(files):
            p = os.path.join(d, f)
            rel = os.path.relpath(p, root).replace(os.sep, "/")
            ext = os.path.splitext(f)[1].lower()
            if f.endswith((".d.ts", ".min.js", ".bundle.js")):
                continue
            group = CODE.get(ext) or _config_group(rel) or ("manifest" if f == "manifest.json" else None)
            if group:
                out.append((rel, group))
    return out


def _call_edges(builder, owner: str, rel: str, line: int) -> list:
    """The CONNECTS_TO / HTTP_CALLS edges of the call the hit belongs to: those of `owner` in `rel` on the line nearest
    to `line` (a multi-line call puts `verify=False` a few lines below the call), CONNECTS_TO first."""
    cands = [e for e in builder.edges.values()
             if e.kind in ("CONNECTS_TO", "HTTP_CALLS") and e.src == owner and e.file == rel and e.line is not None
             and -6 <= line - e.line <= 6]
    if not cands:
        return []
    at = min(abs(e.line - line) for e in cands)
    near = min((e.line for e in cands if abs(e.line - line) == at), default=None)
    return sorted((e for e in cands if e.line == near), key=lambda e: e.kind != "CONNECTS_TO")


def apply(project, builder) -> dict:
    st: dict = defaultdict(int)
    owners = _Owners(builder)
    for rel, group in _files(project.root):
        p = project.root / rel
        try:
            if p.stat().st_size > MAX_BYTES:
                continue
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if group == "manifest":
            facts = manifest_facts(text)
        else:
            if group in CODE.values() and max((len(x) for x in text[:200000].split("\n")), default=0) > 3000:
                continue
            facts = scan_text(text, rel, group)
            if group == "js":
                facts += js_ipc_facts(text)
        if not facts:
            continue
        st["files"] += 1
        test_file = _is_test_path(rel)
        for f in facts:
            if f["kind"] == "grpc-local":
                if not test_file:
                    st["grpc_unix" if f["detail"] == "unix" else "grpc_loopback"] += 1
                continue
            owner = owners.of(rel, f["line"])
            n = builder.nodes.get(owner)
            if test_file or (n is not None and (is_test_node(n) or (n.attrs or {}).get("test_only"))):
                st["tests_skipped"] += 1
                continue
            fact = {k: f[k] for k in ("kind", "line", "lib", "detail", "confidence")}
            if f["kind"] == "tls-verify-off" and "whole process" not in f["detail"]:
                edges = _call_edges(builder, owner, rel, f["line"])
                for edge in edges:
                    edge.attrs = {**(edge.attrs or {}), "tls_verify": False}
                    dn = builder.nodes.get(edge.dst)
                    if dn is not None and dn.kind == "external":
                        dn.attrs = {**(dn.attrs or {}), "tls_verify": False}
                if edges:
                    fact["system"] = edges[0].dst
                    st["tls_resolved"] += 1
            if n.attrs is None:
                n.attrs = {}
            lst = n.attrs.setdefault("insecure_transport", [])
            if any(x["kind"] == fact["kind"] and x["line"] == fact["line"] and x["detail"] == fact["detail"] for x in lst):
                continue
            lst.append(fact)
            st[f["kind"].replace("-", "_")] += 1
    return {k: v for k, v in st.items() if v}
