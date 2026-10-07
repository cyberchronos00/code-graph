"""Cloud and SaaS SDK calls as external nodes (#42 part 2).

Not HTTP hosts (`external:http(s):…` is part 1). A client construction plus an operation names the system:

  external:s3:<bucket-or-env>          boto3 / @aws-sdk/client-s3 / Laravel s3 disks / django-storages
  external:gcs:<bucket-or-env>         google.cloud.storage / @google-cloud/storage
  external:azure-blob:<container-or-env>
  external:aws:<service>[:resource]    sqs, secretsmanager, dynamodb, ses
  external:saas:<provider>             stripe, and (part 3a) sendgrid, mailgun, postmark, resend, twilio, vonage
  external:llm:<provider>              openai / anthropic when the call is not already an attrs.llm_calls edge

Mail and SMS APIs (#42 part 3a) use the same shapes. A literal key is `credential_source=literal` (and
`credential_literal=true`, for #47), an env / config() / settings key is `CREDENTIAL_FROM env:<KEY>`, no key found is
`auth=unknown`. Laravel mailers (config/mail.php + config/services.php) and Django `EMAIL_BACKEND` / `ANYMAIL` are one
shared node per provider, used by every `Mail::` / `send_mail` / notification caller.

CONNECTS_TO carries `via` (library) and `op` (SDK operation). CONFIGURED_BY points at the env key of a
bucket / queue / table. CREDENTIAL_FROM points at an explicit key; a default credential chain is
`auth=ambient` on the node and the edge (no secret value is stored). Callers that are already test
nodes are skipped. An endpoint URL is recorded as `endpoint` and is not also attached as external:http(s).
"""
from __future__ import annotations

import ast
import os
import re

from .external import LOOPBACK, parse_dsn
from .presets import skip_dirs
from .tests_index import is_test_node

SKIP = skip_dirs("common") | {
    "vendor", "target", "build", "dist", ".dart_tool", ".venv", "venv", ".tox", ".mypy_cache",
    ".pytest_cache", ".next", ".output", "out", ".gradle", ".idea", "Pods", ".build", ".swiftpm",
    "DerivedData", "Carthage", "coverage",
}
EXTS = {".py", ".php", ".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx"}
MARK = re.compile(
    r"boto3|google\.cloud|azure\.storage|stripe|STORAGES|DEFAULT_FILE_STORAGE|default_storage|"
    r"@aws-sdk/|@google-cloud/storage|@azure/storage-blob|@anthropic-ai/sdk|\bopenai\b|\banthropic\b|"
    r"Aws\\|Storage::|Flysystem|Stripe\\|filesystems"
)
ENV_PY = re.compile(r"""^(?:os\.)?(?:environ(?:\.get)?|getenv)$""")
AWS_FIELD = {
    "s3": "Bucket", "sqs": "QueueUrl", "secretsmanager": "SecretId", "dynamodb": "TableName", "ses": None,
}
AWS_PROTO = {"s3": "s3", "sqs": "aws", "secretsmanager": "aws", "dynamodb": "aws", "ses": "aws"}
PY_OPS = {
    "s3": {"put_object", "get_object", "delete_object", "head_object", "copy_object", "upload_file",
           "upload_fileobj", "download_file", "list_objects_v2", "create_bucket", "put_object_acl"},
    "sqs": {"send_message", "receive_message", "delete_message", "get_queue_url", "create_queue"},
    "secretsmanager": {"get_secret_value", "create_secret", "put_secret_value", "describe_secret"},
    "dynamodb": {"get_item", "put_item", "query", "scan", "update_item", "delete_item", "batch_get_item"},
    "ses": {"send_email", "send_raw_email"},
}
BUCKET_SETTINGS = {
    "s3": ("AWS_STORAGE_BUCKET_NAME", "AWS_S3_BUCKET_NAME", "AWS_BUCKET"),
    "gcs": ("GS_BUCKET_NAME", "GCS_BUCKET", "GOOGLE_STORAGE_BUCKET"),
    "azure-blob": ("AZURE_CONTAINER", "AZURE_STORAGE_CONTAINER"),
}
TS_CMD = {
    "PutObjectCommand": ("s3", "Bucket", "PutObject"),
    "DeleteObjectCommand": ("s3", "Bucket", "DeleteObject"),
    "GetObjectCommand": ("s3", "Bucket", "GetObject"),
    "HeadObjectCommand": ("s3", "Bucket", "HeadObject"),
    "CopyObjectCommand": ("s3", "Bucket", "CopyObject"),
    "ListObjectsV2Command": ("s3", "Bucket", "ListObjectsV2"),
    "CreateMultipartUploadCommand": ("s3", "Bucket", "CreateMultipartUpload"),
    "UploadPartCommand": ("s3", "Bucket", "UploadPart"),
    "CompleteMultipartUploadCommand": ("s3", "Bucket", "CompleteMultipartUpload"),
    "SendMessageCommand": ("sqs", "QueueUrl", "SendMessage"),
    "ReceiveMessageCommand": ("sqs", "QueueUrl", "ReceiveMessage"),
    "GetSecretValueCommand": ("secretsmanager", "SecretId", "GetSecretValue"),
    "CreateSecretCommand": ("secretsmanager", "Name", "CreateSecret"),
    "GetItemCommand": ("dynamodb", "TableName", "GetItem"),
    "PutItemCommand": ("dynamodb", "TableName", "PutItem"),
    "QueryCommand": ("dynamodb", "TableName", "Query"),
    "UpdateItemCommand": ("dynamodb", "TableName", "UpdateItem"),
    "DeleteItemCommand": ("dynamodb", "TableName", "DeleteItem"),
    "SendEmailCommand": ("ses", None, "SendEmail"),
}
MARK_MAIL = re.compile(
    r"sendgrid|mailgun|postmark|resend|twilio|vonage|nexmo|anymail|EMAIL_BACKEND|Mail::|Mailable|"
    r"Notification|django\.core\.mail|send_mail", re.I)
CONFIG_FILES = ("filesystems.php", "mail.php", "services.php")
_TEST_PATH = re.compile(r"(^|/)(tests?|__tests__|spec)(/|$)|\.(test|spec)\.[cm]?[jt]sx?$|Test\.php$|(^|/)test_.*\.py$")
_CRED_KW = ("aws_access_key_id", "aws_secret_access_key", "aws_session_token", "accessKeyId", "secretAccessKey", "sessionToken")


def _rel(root, path) -> str:
    return os.path.relpath(path, root).replace(os.sep, "/")


def _files(root):
    for dp, dns, fns in os.walk(root):
        dns[:] = [d for d in dns if d not in SKIP and not d.startswith(".")]
        for fn in fns:
            ext = os.path.splitext(fn)[1].lower()
            cfg_php = fn in CONFIG_FILES and os.path.basename(dp) == "config"
            if ext not in EXTS and not cfg_php:
                continue
            p = os.path.join(dp, fn)
            try:
                if os.path.getsize(p) > 1_500_000:
                    continue
                text = open(p, errors="replace").read()
            except OSError:
                continue
            if cfg_php or MARK.search(text) or MARK_MAIL.search(text):
                yield _rel(root, p), text


def _test_path(rel: str) -> bool:
    return bool(_TEST_PATH.search(rel.replace("\\", "/")))


def _prefer_key(keys: list[str]) -> str | None:
    if not keys:
        return None
    def score(k):
        s = 0
        if any(w in k for w in ("BUCKET", "CONTAINER", "QUEUE", "TABLE", "SECRET")):
            s += 3
        if "URL" in k:
            s -= 2
        return s
    return max(keys, key=score)


def _brace(text: str, open_at: int) -> str:
    """Body starting at the '{' at open_at-ish. `open_at` is the index of '{'."""
    if open_at >= len(text) or text[open_at] != "{":
        return ""
    depth, i, n = 0, open_at, len(text)
    while i < n:
        c = text[i]
        if c in "'\"`":
            q = c
            i += 1
            while i < n and text[i] != q:
                i += 2 if text[i] == "\\" else 1
            i += 1
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return text[open_at:i + 1]
        i += 1
    return text[open_at:]


def _env_in(expr: str) -> str | None:
    if not expr:
        return None
    keys = re.findall(r"""(?:env|environment|process\.env)\.([A-Z][A-Z0-9_]*)""", expr)
    keys += re.findall(r"""process\.env\[\s*['\"]([A-Z][A-Z0-9_]*)['\"]\s*\]""", expr)
    keys += re.findall(r"""(?:getenv|env)\(\s*['\"]([A-Z][A-Z0-9_]*)['\"]""", expr)
    keys += re.findall(r"""\$_(?:ENV|SERVER)\[\s*['\"]([A-Z][A-Z0-9_]*)['\"]""", expr)
    return _prefer_key(keys)


def _lit_in(expr: str) -> str | None:
    if not expr:
        return None
    m = re.search(r"""['\"]([A-Za-z0-9][A-Za-z0-9._-]{0,200})['\"]""", expr)
    if not m:
        return None
    v = m.group(1)
    if v.startswith(("http://", "https://")) or "{" in v or "$" in v:
        return None
    return v


def _resource(expr: str, methods: dict | None = None):
    """('lit', name) | ('env', KEY) | None from a Bucket / QueueUrl / container expression."""
    if not expr:
        return None
    ek = _env_in(expr)
    if ek:
        return ("env", ek)
    m = re.search(r"""this\.(\w+)\s*\(""", expr)
    if m and methods and m.group(1) in methods:
        return ("env", methods[m.group(1)])
    lit = _lit_in(expr)
    if lit and lit.lower() not in LOOPBACK:
        return ("lit", lit)
    # QueueUrl https://sqs..../name -> last segment, not an HTTP host node
    um = re.search(r"""https?://[^\s'\"]+""", expr)
    if um:
        d = parse_dsn(um.group(0))
        if d and d.get("resource"):
            seg = d["resource"].rstrip("/").rsplit("/", 1)[-1]
            if seg and seg.lower() not in LOOPBACK:
                return ("lit", seg)
    return None


def _prop(text: str, key: str) -> str | None:
    m = re.search(rf"""\b{key}\s*:\s*""", text)
    if not m:
        return None
    i, n, depth = m.end(), len(text), 0
    start = i
    while i < n and i < start + 400:
        c = text[i]
        if c in "'\"`":
            q = c
            i += 1
            while i < n and text[i] != q:
                i += 2 if text[i] == "\\" else 1
            i += 1
            continue
        if c in "([{":
            depth += 1
        elif c in ")]}":
            if depth == 0:
                break
            depth -= 1
        elif c == "," and depth == 0:
            break
        elif c == "\n" and depth == 0:
            break
        i += 1
    return text[start:i]


def _php_prop(text: str, key: str) -> str | None:
    m = re.search(rf"""['\"]{key}['\"]\s*=>\s*""", text)
    if not m:
        return None
    i, n, depth = m.end(), len(text), 0
    start = i
    while i < n and i < start + 300:
        c = text[i]
        if c in "'\"":
            q = c
            i += 1
            while i < n and text[i] != q:
                i += 2 if text[i] == "\\" else 1
            i += 1
            continue
        if c in "([{":
            depth += 1
        elif c in ")]}":
            if depth == 0:
                break
            depth -= 1
        elif c == "," and depth == 0:
            break
        i += 1
    return text[start:i]


def _line_at(text: str, idx: int) -> int:
    return text.count("\n", 0, idx) + 1


def _target(protocol: str, service: str | None, resource) -> str | None:
    if protocol in ("s3", "gcs", "azure-blob"):
        if not resource:
            return None
        return resource[1] if resource[0] == "lit" else f"env:{resource[1]}"
    if protocol == "aws":
        if not service:
            return None
        if not resource:
            return service
        extra = resource[1] if resource[0] == "lit" else f"env:{resource[1]}"
        return f"{service}:{extra}"
    if protocol in ("saas", "llm"):
        return service
    return None


def _fact(rel, line, protocol, service, resource, op, via, auth="ambient", creds=None, endpoint=None):
    target = _target(protocol, service, resource)
    if not target or _test_path(rel):
        return None
    return {
        "file": rel, "line": line, "protocol": protocol, "target": target, "op": op, "via": via,
        "auth": auth, "creds": [c for c in (creds or []) if c],
        "resource": resource[1] if resource else None,
        "resource_env": resource[1] if resource and resource[0] == "env" else None,
        "endpoint": endpoint, "confidence": "exact" if resource and resource[0] == "lit" else "resolved",
        "address_source": "literal" if resource and resource[0] == "lit" else "env" if resource else "sdk",
    }


# ---------------------------------------------------------------- Python
def _py_value(node):
    """('lit', s) | ('env', KEY) | None."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return ("lit", node.value)
    if isinstance(node, ast.Subscript):
        base = node.value
        if isinstance(base, ast.Attribute) and base.attr == "environ":
            sl = node.slice
            if isinstance(sl, ast.Constant) and isinstance(sl.value, str):
                return ("env", sl.value)
    if isinstance(node, ast.Call):
        fn = node.func
        name = fn.attr if isinstance(fn, ast.Attribute) else fn.id if isinstance(fn, ast.Name) else ""
        recv = fn.value if isinstance(fn, ast.Attribute) else None
        rname = recv.attr if isinstance(recv, ast.Attribute) else recv.id if isinstance(recv, ast.Name) else ""
        if node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
            key = node.args[0].value
            if (name == "get" and rname == "environ") or name in ("getenv", "env") or (rname in ("os",) and name == "getenv"):
                return ("env", key)
            if name in ("str", "int") :
                return _py_value(node.args[0])
    return None


def _py_kw(call, name):
    for k in call.keywords:
        if k.arg == name:
            return k.value
    return None


def _py_creds(call):
    keys, explicit = [], False
    for arg in _CRED_KW:
        v = _py_kw(call, arg)
        if v is None:
            continue
        explicit = True
        pv = _py_value(v)
        if pv and pv[0] == "env":
            keys.append(pv[1])
    return explicit, keys


def _py_service_call(call):
    fn = call.func
    if not isinstance(fn, ast.Attribute) or fn.attr not in ("client", "resource"):
        return None
    recv = fn.value
    rname = recv.id if isinstance(recv, ast.Name) else recv.attr if isinstance(recv, ast.Attribute) else ""
    if rname not in ("boto3", "session", "Session", "_session"):
        # boto3.session.Session().client: the receiver is a Call to Session
        if not (isinstance(recv, ast.Call) and isinstance(recv.func, ast.Attribute) and recv.func.attr == "Session"):
            if rname != "boto3":
                return None
    if not call.args or not isinstance(call.args[0], ast.Constant) or not isinstance(call.args[0].value, str):
        return None
    svc = call.args[0].value
    return svc if svc in AWS_FIELD else None


def _backend_proto(name: str | None):
    if not name:
        return None
    n = name.lower()
    if any(x in n for x in ("s3boto", "s3", "boto")):
        return "s3"
    if any(x in n for x in ("gcloud", "google")):
        return "gcs"
    if "azure" in n:
        return "azure-blob"
    return None


def _collect_py(rel: str, text: str, storage: dict):
    if not text.lstrip().startswith(("import ", "from ", "#", "\"\"\"", "'''", "class ", "def ", "os", "STORAGES", "AWS_", "GS_")) and "boto3" not in text and "stripe" not in text and "storage" not in text and "azure" not in text:
        pass
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []
    facts = []
    # django-storages settings
    mod_env = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            name = node.targets[0].id
            if name in ("STORAGES", "DEFAULT_FILE_STORAGE") or name in {k for ks in BUCKET_SETTINGS.values() for k in ks}:
                pv = _py_value(node.value)
                if pv:
                    mod_env[name] = pv
                elif name == "STORAGES" and isinstance(node.value, ast.Dict):
                    storage.update(_storages(node.value))
                elif name == "DEFAULT_FILE_STORAGE" and isinstance(node.value, ast.Constant):
                    proto = _backend_proto(str(node.value.value))
                    if proto:
                        storage.setdefault("default", {"protocol": proto, "resource": None, "creds": []})
    for proto, keys in BUCKET_SETTINGS.items():
        for spec in storage.values():
            if spec.get("protocol") == proto and spec.get("resource") is None:
                for k in keys:
                    if k in mod_env:
                        spec["resource"] = mod_env[k]
                        break
    # stripe.api_key
    stripe_creds = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Attribute):
            t = node.targets[0]
            if t.attr == "api_key" and isinstance(t.value, ast.Name) and t.value.id == "stripe":
                pv = _py_value(node.value)
                if pv and pv[0] == "env":
                    stripe_creds.append(pv[1])
    for n in ast.walk(tree):
        for c in ast.iter_child_nodes(n):
            setattr(c, "cg_parent", n)

    def _scope(node):
        p = node
        while p is not None and not isinstance(p, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Module)):
            p = getattr(p, "cg_parent", None)
        return p

    clients: dict = {}
    azure_cred: dict = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name) and isinstance(node.value, ast.Call):
            svc = _py_service_call(node.value)
            if svc:
                explicit, keys = _py_creds(node.value)
                sc = _scope(node)
                if sc is not None:
                    clients[(id(sc), node.targets[0].id)] = (svc, "explicit" if explicit else "ambient", keys)
            fn = node.value.func
            if isinstance(fn, ast.Attribute) and fn.attr == "from_connection_string" and node.value.args:
                cv = _py_value(node.value.args[0])
                sc = _scope(node)
                if cv and cv[0] == "env" and sc is not None:
                    azure_cred[(id(sc), node.targets[0].id)] = cv[1]

    def _client_of(node, name):
        p = _scope(node)
        while p is not None:
            hit = clients.get((id(p), name))
            if hit:
                return hit
            if isinstance(p, ast.Module):
                break
            parent = getattr(p, "cg_parent", None)
            p = parent if isinstance(parent, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Module)) else _scope(parent) if parent is not None else None
        return None

    def add_aws(call, svc, auth, creds, op):
        field = AWS_FIELD.get(svc)
        res = None
        if field:
            raw = _py_kw(call, field)
            if raw is None and call.args and svc == "s3" and op == "Bucket":
                raw = call.args[0]
            if raw is not None:
                pv = _py_value(raw)
                if pv and pv[0] == "lit" and "://" in pv[1]:
                    res = _resource(pv[1])
                elif pv:
                    res = pv
        endpoint = None
        ev = _py_kw(call, "endpoint_url")
        if ev is not None:
            pv = _py_value(ev)
            if pv and pv[0] == "lit" and "://" in pv[1]:
                endpoint = pv[1]
        f = _fact(rel, call.lineno, AWS_PROTO[svc], svc if AWS_PROTO[svc] == "aws" else None, res, op, "boto3", auth, creds, endpoint)
        if f:
            facts.append(f)

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        op = node.func.attr
        recv = node.func.value
        # boto3.client("s3").put_object(...)
        if isinstance(recv, ast.Call):
            svc = _py_service_call(recv)
            if svc and op in PY_OPS.get(svc, ()):
                explicit, keys = _py_creds(recv)
                add_aws(node, svc, "explicit" if explicit else "ambient", keys, op)
                continue
            # resource.Bucket("name").put_object
            if isinstance(recv.func, ast.Attribute) and recv.func.attr == "Bucket" and recv.args:
                pv = _py_value(recv.args[0])
                f = _fact(rel, node.lineno, "s3", None, pv, op, "boto3")
                if f:
                    facts.append(f)
                continue
        bound = _client_of(node, recv.id) if isinstance(recv, ast.Name) else None
        if bound and op in PY_OPS.get(bound[0], ()):
            svc, auth, keys = bound
            add_aws(node, svc, auth, keys, op)
            continue
        # storage.Client().bucket("name") / client.bucket("name")
        if op == "bucket" and "google.cloud" in text:
            inner = recv if isinstance(recv, ast.Call) else None
            ok = False
            if inner and isinstance(inner.func, ast.Attribute) and inner.func.attr == "Client":
                base = inner.func.value
                ok = isinstance(base, ast.Name) and base.id in ("storage", "gcs")
            if isinstance(recv, ast.Name) and recv.id in ("storage", "client", "gcs"):
                ok = True
            if ok and node.args:
                pv = _py_value(node.args[0])
                f = _fact(rel, node.lineno, "gcs", None, pv, "bucket", "google-cloud-storage")
                if f:
                    facts.append(f)
            continue
        if op == "get_container_client" and node.args and "azure.storage" in text:
            pv = _py_value(node.args[0])
            creds = []
            auth = "ambient"
            # walk receiver for from_connection_string(env)
            cur = recv
            if isinstance(recv, ast.Name):
                p = _scope(node)
                while p is not None:
                    hit = azure_cred.get((id(p), recv.id))
                    if hit:
                        creds, auth = [hit], "explicit"
                        break
                    if isinstance(p, ast.Module):
                        break
                    parent = getattr(p, "cg_parent", None)
                    p = parent if isinstance(parent, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Module)) else None
            if isinstance(cur, ast.Call) and isinstance(cur.func, ast.Attribute) and cur.func.attr == "from_connection_string" and cur.args:
                cv = _py_value(cur.args[0])
                if cv and cv[0] == "env":
                    creds = [cv[1]]
                    auth = "explicit"
            f = _fact(rel, node.lineno, "azure-blob", None, pv, "get_container_client", "azure-storage-blob", auth, creds)
            if f:
                facts.append(f)
            continue
        # stripe.Charge.create / stripe.PaymentIntent.create
        parts = []
        cur = node.func
        while isinstance(cur, ast.Attribute):
            parts.append(cur.attr)
            cur = cur.value
        if isinstance(cur, ast.Name):
            parts.append(cur.id)
        parts.reverse()
        if parts and parts[0] == "stripe" and parts[-1] == "create":
            op_name = ".".join(parts[1:])
            f = _fact(rel, node.lineno, "saas", "stripe", None, op_name, "stripe",
                      "explicit" if stripe_creds else "ambient", stripe_creds)
            if f:
                facts.append(f)
        elif len(parts) == 2 and parts[1] == "create" and parts[0][:1].isupper():
            # `from stripe import Charge` is only safe when stripe is imported; require the name Charge/PaymentIntent
            if parts[0] in ("Charge", "PaymentIntent", "Customer", "Subscription", "Refund"):
                if "stripe" in text:
                    f = _fact(rel, node.lineno, "saas", "stripe", None, f"{parts[0]}.create", "stripe",
                              "explicit" if stripe_creds else "ambient", stripe_creds)
                    if f:
                        facts.append(f)
        # default_storage.save / open / delete / exists
        if isinstance(recv, ast.Name) and recv.id == "default_storage" and op in ("save", "open", "delete", "exists", "url", "listdir"):
            spec = storage.get("default")
            if spec and spec.get("protocol"):
                f = _fact(rel, node.lineno, spec["protocol"], None, spec.get("resource"), op, "django-storages",
                          "explicit" if spec.get("creds") else "ambient", spec.get("creds") or [])
                if f:
                    facts.append(f)
    return facts


def _storages(d: ast.Dict) -> dict:
    out = {}
    for k, v in zip(d.keys, d.values):
        if not isinstance(k, ast.Constant) or not isinstance(k.value, str) or not isinstance(v, ast.Dict):
            continue
        backend, resource, creds = None, None, []
        for kk, vv in zip(v.keys, v.values):
            if not isinstance(kk, ast.Constant):
                continue
            if kk.value == "BACKEND":
                pv = _py_value(vv)
                backend = pv[1] if pv and pv[0] == "lit" else vv.value if isinstance(vv, ast.Constant) else None
            if kk.value == "OPTIONS" and isinstance(vv, ast.Dict):
                for ok, ov in zip(vv.keys, vv.values):
                    if isinstance(ok, ast.Constant) and ok.value in ("bucket_name", "azure_container", "container"):
                        resource = _py_value(ov)
        proto = _backend_proto(str(backend) if backend else None)
        if proto:
            out[k.value] = {"protocol": proto, "resource": resource, "creds": creds}
    return out


# ---------------------------------------------------------------- JS / TS
def _ts_methods(text: str) -> dict:
    out = {}
    for m in re.finditer(r"""(?:public|private|protected|async|static|\s)*\b([A-Za-z_]\w*)\s*\([^;]*\)\s*\{""", text):
        name = m.group(1)
        if name in ("if", "for", "while", "switch", "catch", "function"):
            continue
        brace = text.find("{", m.end() - 1)
        body = _brace(text, brace)[:2000]
        keys = re.findall(r"""(?:env|environment|process\.env)\.([A-Z][A-Z0-9_]*)""", body)
        pref = _prefer_key(keys)
        if pref:
            out[name] = pref
    return out


def _explicit_creds_near(text: str) -> tuple[str, list]:
    if re.search(r"""accessKeyId|secretAccessKey|aws_access_key_id""", text):
        keys = re.findall(r"""(?:env|environment|process\.env)\.(AWS_(?:ACCESS_KEY_ID|SECRET_ACCESS_KEY|SESSION_TOKEN))""", text)
        return ("explicit" if keys or "accessKeyId" in text else "ambient"), list(dict.fromkeys(keys))
    return "ambient", []


def _collect_ts(rel: str, text: str):
    facts = []
    methods = _ts_methods(text) if ("@aws-sdk/" in text or "getBucket" in text) else {}
    s3 = "@aws-sdk/client-s3" in text or "@aws-sdk/lib-storage" in text
    if s3 or any(k in text for k in TS_CMD):
        auth, creds = _explicit_creds_near(text)
        body = text
        for m in re.finditer(r"""new\s+(?:[\w$.]+\.)?(\w+Command)\s*\(""", body):
            spec = TS_CMD.get(m.group(1))
            if not spec:
                continue
            proto_s, field, op = spec
            # params are often declared just above the command; do not read a sliced env key
            window = body[max(0, m.start() - 900):m.end() + 500]
            res = _resource(_prop(window, field) or "", methods) if field else None
            if proto_s == "s3" and (res is None or (res[0] == "env" and res[1].endswith("_"))):
                res = _resource("this.getBucket()", methods) or res
            f = _fact(rel, _line_at(text, m.start()), "s3" if proto_s == "s3" else "aws",
                      None if proto_s == "s3" else proto_s, res, op,
                      "@aws-sdk/client-s3" if proto_s == "s3" else f"@aws-sdk/client-{proto_s}",
                      auth, creds, None)
            if f:
                facts.append(f)
        if s3:
            for m in re.finditer(r"""new\s+(?:[\w$.]+\.)?Upload\s*\(""", body):
                window = body[m.end():m.end() + 800]
                res = _resource(_prop(window, "Bucket") or "", methods) or _resource("this.getBucket()", methods)
                f = _fact(rel, _line_at(text, m.start()), "s3", None, res, "Upload", "@aws-sdk/lib-storage", auth, creds)
                if f:
                    facts.append(f)
            for m in re.finditer(r"""createPresignedPost\s*\(""", body):
                window = body[max(0, m.start() - 800):m.start()]
                res = _resource(_prop(window, "Bucket") or "", methods) or _resource("this.getBucket()", methods)
                f = _fact(rel, _line_at(text, m.start()), "s3", None, res, "createPresignedPost", "@aws-sdk/s3-presigned-post", auth, creds)
                if f:
                    facts.append(f)
            for m in re.finditer(r"""new\s+(?:[\w$.]+\.)?S3Client\s*\(""", body):
                window = body[m.end():m.end() + 500]
                res = _resource(_prop(window, "Bucket") or "", methods) or _resource("this.getBucket()", methods)
                ep = _prop(window, "endpoint")
                endpoint = None
                if ep:
                    lit = re.search(r"""['\"](https?://[^'\"]+)['\"]""", ep)
                    endpoint = lit.group(1) if lit else None
                    if endpoint:
                        d = parse_dsn(endpoint)
                        if d and (d.get("host") or "").lower() in LOOPBACK:
                            endpoint = None
                f = _fact(rel, _line_at(text, m.start()), "s3", None, res, "S3Client", "@aws-sdk/client-s3", auth, creds, endpoint)
                if f:
                    facts.append(f)
    if "@google-cloud/storage" in text:
        for m in re.finditer(r"""\.bucket\s*\(\s*([^)]{0,200})\)""", text):
            res = _resource(m.group(1))
            f = _fact(rel, _line_at(text, m.start()), "gcs", None, res, "bucket", "@google-cloud/storage")
            if f:
                facts.append(f)
    if "@azure/storage-blob" in text:
        creds, auth = [], "ambient"
        cm = re.search(r"""fromConnectionString\s*\(\s*([^)]+)\)""", text)
        if cm:
            ek = _env_in(cm.group(1))
            if ek:
                creds, auth = [ek], "explicit"
        for m in re.finditer(r"""\.getContainerClient\s*\(\s*([^)]{0,200})\)""", text):
            res = _resource(m.group(1))
            f = _fact(rel, _line_at(text, m.start()), "azure-blob", None, res, "getContainerClient", "@azure/storage-blob", auth, creds)
            if f:
                facts.append(f)
    if re.search(r"""['\"]stripe['\"]|from\s+['\"]stripe['\"]|require\(\s*['\"]stripe['\"]\s*\)""", text):
        creds = re.findall(r"""(?:env|environment|process\.env)\.(STRIPE_[A-Z0-9_]*KEY[A-Z0-9_]*)""", text)
        auth = "explicit" if creds else "ambient"
        for m in re.finditer(r"""\.(charges|paymentIntents|customers|checkout)\.(\w+)\s*\(""", text):
            f = _fact(rel, _line_at(text, m.start()), "saas", "stripe", None, f"{m.group(1)}.{m.group(2)}", "stripe", auth, creds)
            if f:
                facts.append(f)
        if "new Stripe" in text or "new Stripe(" in text:
            if not any(x["op"].startswith("charges") or "paymentIntents" in x["op"] for x in facts if x["file"] == rel):
                m = re.search(r"""new\s+Stripe\s*\(""", text)
                if m:
                    f = _fact(rel, _line_at(text, m.start()), "saas", "stripe", None, "Stripe", "stripe", auth, creds)
                    if f:
                        facts.append(f)
    # OpenAI / Anthropic clients that the TS extractor does not record as attrs.llm_calls
    if re.search(r"""['\"]openai['\"]|@anthropic-ai/sdk|['\"]anthropic['\"]""", text):
        for prov, op_re, via in (
            ("openai", r"""\.chat\.completions\.create\s*\(""", "openai"),
            ("anthropic", r"""\.messages\.create\s*\(""", "anthropic"),
        ):
            creds = re.findall(rf"""(?:env|environment|process\.env)\.((?:OPENAI|ANTHROPIC)_[A-Z0-9_]*KEY[A-Z0-9_]*)""", text) if prov else []
            for m in re.finditer(op_re, text):
                f = _fact(rel, _line_at(text, m.start()), "llm", prov, None, "chat" if prov == "openai" else "messages", via,
                          "explicit" if creds else "ambient", creds)
                if f:
                    facts.append(f)
    return facts


# ---------------------------------------------------------------- PHP
def _php_disks(text: str) -> tuple[dict, str | None]:
    disks, default = {}, None
    dm = re.search(r"""['\"]default['\"]\s*=>\s*([^,\n]+)""", text)
    if dm:
        lit = re.search(r"""['\"](\w+)['\"]\s*\)?\s*$""", dm.group(1).strip())
        # env('FILESYSTEM_DISK', 's3') -> default literal fallback
        fb = re.search(r"""env\(\s*['\"][^'\"]+['\"]\s*,\s*['\"](\w+)['\"]\s*\)""", dm.group(1))
        if fb:
            default = fb.group(1)
        elif re.fullmatch(r"""['\"](\w+)['\"]""", dm.group(1).strip()):
            default = dm.group(1).strip().strip("'\"")
        elif lit and "env" not in dm.group(1):
            default = lit.group(1)
    # each 'name' => [ ... ] at disk level: scan from 'disks'
    i = text.find("'disks'")
    if i < 0:
        i = text.find('"disks"')
    if i < 0:
        return disks, default
    br = text.find("[", i)
    body = _php_array(text, br)
    # top-level keys
    j, n, depth = 0, len(body), 0
    while j < n:
        m = re.match(r"""\s*['\"](\w+)['\"]\s*=>\s*\[""", body[j:])
        if not m or depth != 1:
            c = body[j]
            if c == "[":
                depth += 1
            elif c == "]":
                depth -= 1
            j += 1
            continue
        name = m.group(1)
        open_b = j + m.end() - 1
        inner = _php_array(body, open_b)
        driver = _php_prop(inner, "driver")
        drv = _lit_in(driver or "")
        proto = {"s3": "s3", "gcs": "gcs", "google": "gcs", "azure": "azure-blob"}.get((drv or "").lower())
        if proto:
            field = "container" if proto == "azure-blob" else "bucket"
            res = _resource(_php_prop(inner, field) or "")
            creds = []
            for ck in ("key", "secret"):
                ek = _env_in(_php_prop(inner, ck) or "")
                if ek:
                    creds.append(ek)
            disks[name] = {"protocol": proto, "resource": res, "creds": creds,
                           "auth": "explicit" if creds else "ambient"}
        j = open_b + len(inner)
    return disks, default


def _php_array(text: str, open_at: int) -> str:
    if open_at < 0 or open_at >= len(text) or text[open_at] != "[":
        return ""
    depth, i, n = 0, open_at, len(text)
    while i < n:
        c = text[i]
        if c in "'\"":
            q = c
            i += 1
            while i < n and text[i] != q:
                i += 2 if text[i] == "\\" else 1
            i += 1
            continue
        if c == "[":
            depth += 1
        elif c == "]":
            depth -= 1
            if depth == 0:
                return text[open_at:i + 1]
        i += 1
    return text[open_at:]


def _collect_php(rel: str, text: str, disks: dict, default: str | None):
    facts = []
    if rel.endswith("filesystems.php") or "disks" in text and "driver" in text and rel.endswith(".php") and "filesystems" in rel:
        found, dflt = _php_disks(text)
        disks.update(found)
        if dflt:
            default = dflt
    for m in re.finditer(r"""Storage::disk\(\s*['\"](\w+)['\"]\s*\)(?:->(\w+))?""", text):
        spec = disks.get(m.group(1))
        if not spec:
            continue
        f = _fact(rel, _line_at(text, m.start()), spec["protocol"], None, spec.get("resource"),
                  m.group(2) or "disk", "laravel", spec.get("auth") or "ambient", spec.get("creds"))
        if f:
            facts.append(f)
    if default and default in disks:
        spec = disks[default]
        for m in re.finditer(r"""Storage::(put|get|exists|delete|copy|move|url|size)\s*\(""", text):
            f = _fact(rel, _line_at(text, m.start()), spec["protocol"], None, spec.get("resource"),
                      m.group(1), "laravel", spec.get("auth") or "ambient", spec.get("creds"))
            if f:
                facts.append(f)
    if "Aws\\" in text or "AwsS3" in text:
        for m in re.finditer(r"""->(putObject|getObject|deleteObject|headObject|copyObject|sendMessage|getSecretValue|putItem|getItem)\s*\(""", text):
            op = m.group(1)
            window = text[m.end():m.end() + 400]
            field = {"putObject": "Bucket", "getObject": "Bucket", "deleteObject": "Bucket", "headObject": "Bucket",
                     "copyObject": "Bucket", "sendMessage": "QueueUrl", "getSecretValue": "SecretId",
                     "putItem": "TableName", "getItem": "TableName"}[op]
            res = _resource(_php_prop(window, field) or "")
            svc = {"QueueUrl": "sqs", "SecretId": "secretsmanager", "TableName": "dynamodb"}.get(field, "s3")
            proto = "s3" if svc == "s3" else "aws"
            # ambient unless the nearest new S3Client([...]) contains 'key'
            ctor = text[max(0, m.start() - 600):m.start()]
            explicit = bool(re.search(r"""['\"](?:key|credentials|secret)['\"]\s*=>""", ctor))
            creds = re.findall(r"""env\(\s*['\"](AWS_(?:ACCESS_KEY_ID|SECRET_ACCESS_KEY))['\"]""", ctor) if explicit else []
            f = _fact(rel, _line_at(text, m.start()), proto, None if proto == "s3" else svc, res, op, "aws-sdk-php",
                      "explicit" if explicit else "ambient", creds)
            if f:
                facts.append(f)
        for m in re.finditer(r"""new\s+\\?(?:League\\Flysystem\\AwsS3V3\\)?AwsS3V3Adapter\s*\(""", text):
            window = text[m.end():m.end() + 300]
            lit = re.search(r"""['\"]([A-Za-z0-9._-]{1,120})['\"]""", window)
            # second string arg is the bucket (first may be $client)
            lits = re.findall(r"""['\"]([A-Za-z0-9._-]{1,120})['\"]""", window)
            res = ("lit", lits[-1]) if lits else None
            f = _fact(rel, _line_at(text, m.start()), "s3", None, res, "AwsS3V3Adapter", "flysystem")
            if f:
                facts.append(f)
    if "Stripe\\" in text:
        creds = re.findall(r"""(?:env|getenv)\(\s*['\"](STRIPE_[A-Z0-9_]+)['\"]""", text)
        auth = "explicit" if creds else "ambient"
        for m in re.finditer(r"""Stripe\\(\w+)::create\s*\(""", text):
            f = _fact(rel, _line_at(text, m.start()), "saas", "stripe", None, f"{m.group(1)}.create", "stripe-php", auth, creds)
            if f:
                facts.append(f)
    return facts, default


# ---------------------------------------------------------------- mail and SMS APIs (#42 part 3a)
MAIL_PROVIDERS = ("sendgrid", "mailgun", "postmark", "resend", "twilio", "vonage")
IGNORED_MAILERS = {"smtp", "sendmail", "log", "array", "failover", "roundrobin", "mailpit", "mailtrap"}
LARAVEL_MAIL_TRANSPORT = {"mailgun": "mailgun", "postmark": "postmark", "resend": "resend", "ses": "ses", "ses-v2": "ses"}
SECRET_FIELD = re.compile(r"key|token|secret|password|sid|auth", re.I)
ANYMAIL_ESP = {
    "amazon_ses": "ses", "sendgrid": "sendgrid", "mailgun": "mailgun", "postmark": "postmark", "resend": "resend",
    "brevo": "brevo", "mailersend": "mailersend", "mailjet": "mailjet", "mandrill": "mandrill", "postal": "postal",
    "sparkpost": "sparkpost", "unisender_go": "unisender-go", "scaleway": "scaleway",
}


def _env_keys(expr: str) -> list[str]:
    keys: list[str] = []
    for rx in (
        r"""(?:process\.env|import\.meta\.env|Bun\.env)\.([A-Z][A-Z0-9_]*)""",
        r"""(?:process\.env|import\.meta\.env)\[\s*['\"]([A-Z][A-Z0-9_]*)['\"]\s*\]""",
        r"""(?:getenv|\benv)\(\s*['\"]([A-Z][A-Z0-9_]*)['\"]""",
        r"""environ(?:\.get)?\s*[\[(]\s*['\"]([A-Z][A-Z0-9_]*)['\"]""",
        r"""\$_(?:ENV|SERVER)\[\s*['\"]([A-Z][A-Z0-9_]*)['\"]""",
    ):
        keys += re.findall(rx, expr or "")
    return list(dict.fromkeys(keys))


def _args_at(text: str, open_at: int) -> str:
    """Text between the '(' at open_at and its matching ')'."""
    if open_at < 0 or open_at >= len(text) or text[open_at] != "(":
        return ""
    depth, i, n = 0, open_at, len(text)
    while i < n:
        c = text[i]
        if c in "'\"`":
            q = c
            i += 1
            while i < n and text[i] != q:
                i += 2 if text[i] == "\\" else 1
            i += 1
            continue
        if c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                return text[open_at + 1:i]
        i += 1
    return text[open_at + 1:open_at + 400]


def _cred_expr(expr: str, text: str, cfg: dict | None = None, settings: dict | None = None):
    """('env', [KEY...]) | ('literal', []) | ('none', []) from the key / token argument of a client."""
    expr = (expr or "").strip()
    if re.fullmatch(r"\$?[A-Za-z_]\w*", expr):
        name = expr.lstrip("$")
        m = re.search(r"(?:(?:const|let|var)\s+|\bself\.|\bthis\.|\$this->|\$)?\b%s\b\s*(?::[^=\n]+)?=\s*([^;\n]+)" % re.escape(name), text)
        if m and "==" not in m.group(0):
            expr = m.group(1)
    keys = _env_keys(expr)
    if keys:
        return ("env", keys)
    mc = re.search(r"""\bconfig\(\s*['\"]([\w.\-]+)['\"]""", expr)
    if mc:
        return (cfg or {}).get(mc.group(1)) or ("none", [])
    ms = re.search(r"""\bsettings\.([A-Z][A-Z0-9_]*)""", expr)
    if ms:
        return (settings or {}).get(ms.group(1)) or ("none", [])
    if re.search(r"""['\"][^'\"\s]{6,}['\"]""", expr):
        for lit in re.findall(r"""['\"]([^'\"\s]{6,})['\"]""", expr):
            if not lit.startswith(("http://", "https://")):
                return ("literal", [])
    return ("none", [])


def _merge_cred(*creds):
    """First env / config result, else literal, else none."""
    for kind in ("env", "literal"):
        for c in creds:
            if c and c[0] == kind:
                return c
    return ("none", [])


def _mail_fact(rel, line, provider, op, via, cred, resource=None, endpoint=None):
    proto, svc = ("aws", "ses") if provider == "ses" else ("saas", provider)
    kind, keys = cred
    if provider == "ses" and kind == "none":
        auth = "ambient"
    else:
        auth = {"env": "explicit", "literal": "explicit"}.get(kind, "unknown")
    f = _fact(rel, line, proto, svc, None, op, via, auth, keys if kind == "env" else [], endpoint)
    if not f:
        return None
    f["confidence"] = "exact"
    f["address_source"] = "sdk"
    f["ensure_env"] = True
    if kind == "literal":
        f["literal_credential"] = True
    if resource:
        f["resource"] = resource
    return f


def _res_expr(expr: str):
    """A Mailgun domain argument as an attribute value ('example.com' or 'env:KEY') or None."""
    ek = _env_in(expr or "")
    if ek:
        return f"env:{ek}"
    lit = _lit_in(expr or "")
    return lit


def _services_php(text: str) -> dict:
    """config/services.php -> {'services.<provider>.<field>': ('env', [KEY]) | ('literal', [])}."""
    out = {}
    i = text.find("return")
    br = text.find("[", i if i >= 0 else 0)
    body = _php_array(text, br)
    j, n, depth = 0, len(body), 0
    while j < n:
        m = re.match(r"""\s*['\"]([\w\-]+)['\"]\s*=>\s*\[""", body[j:])
        if not m or depth != 1:
            c = body[j]
            depth += 1 if c == "[" else -1 if c == "]" else 0
            j += 1
            continue
        name = m.group(1)
        open_b = j + m.end() - 1
        inner = _php_array(body, open_b)
        for fm in re.finditer(r"""['\"](\w+)['\"]\s*=>\s*([^,\n\]]+)""", inner[1:-1]):
            expr = fm.group(2)
            keys = _env_keys(expr)
            if keys:
                out[f"services.{name}.{fm.group(1)}"] = ("env", keys)
            elif re.fullmatch(r"""\s*['\"][^'\"\s]{6,}['\"]\s*""", expr) and SECRET_FIELD.search(fm.group(1)):
                out[f"services.{name}.{fm.group(1)}"] = ("literal", [])
        j = open_b + len(inner)
    return out


def _service_cred(services: dict, name: str):
    """The credential fields of config/services.php's `name` block (key / token / secret / sid ...)."""
    envs, literal = [], False
    for k, v in services.items():
        parts = k.split(".")
        if len(parts) == 3 and parts[1] == name and SECRET_FIELD.search(parts[2]):
            if v[0] == "env":
                envs += [e for e in v[1] if e not in envs]
            else:
                literal = True
    if envs:
        return ("env", envs)
    return ("literal", []) if literal else ("none", [])


def _service_domain(services: dict, name: str):
    v = services.get(f"services.{name}.domain")
    return f"env:{v[1][0]}" if v and v[0] == "env" else None


def _php_mailers(text: str) -> tuple[dict, str | None, str | None]:
    """config/mail.php -> ({mailer: transport}, default mailer, env key of the default)."""
    default, env_key = None, None
    dm = re.search(r"""['\"]default['\"]\s*=>\s*([^\n]+)""", text)
    if dm:
        ek = _env_keys(dm.group(1))
        env_key = ek[0] if ek else None
        fb = re.search(r"""env\(\s*['\"][^'\"]+['\"]\s*,\s*['\"]([\w\-]+)['\"]\s*\)""", dm.group(1))
        lit = re.match(r"""\s*['\"]([\w\-]+)['\"]""", dm.group(1))
        default = fb.group(1) if fb else lit.group(1) if lit else None
    mailers = {}
    i = text.find("'mailers'")
    if i < 0:
        i = text.find('"mailers"')
    if i < 0:
        return mailers, default, env_key
    body = _php_array(text, text.find("[", i))
    j, n, depth = 0, len(body), 0
    while j < n:
        m = re.match(r"""\s*['\"]([\w\-]+)['\"]\s*=>\s*\[""", body[j:])
        if not m or depth != 1:
            c = body[j]
            depth += 1 if c == "[" else -1 if c == "]" else 0
            j += 1
            continue
        open_b = j + m.end() - 1
        inner = _php_array(body, open_b)
        tm = re.search(r"""['\"]transport['\"]\s*=>\s*['\"]([\w\-]+)['\"]""", inner)
        mailers[m.group(1)] = tm.group(1) if tm else m.group(1)
        j = open_b + len(inner)
    return mailers, default, env_key


def _env_file_value(root, key: str) -> str | None:
    for name in (".env", ".env.example"):
        try:
            for line in open(os.path.join(root, name), errors="replace"):
                m = re.match(rf"\s*{key}\s*=\s*['\"]?([\w\-]+)", line)
                if m:
                    return m.group(1)
        except OSError:
            continue
    return None


def _collect_laravel_mail(rel: str, text: str, ctx: dict) -> list:
    facts = []
    mailers, default, services = ctx["mailers"], ctx["mail_default"], ctx["services"]

    def transport_fact(line, mailer, op, via):
        tr = LARAVEL_MAIL_TRANSPORT.get(mailers.get(mailer, mailer))
        if not tr:
            return None
        if tr == "ses":
            return _mail_fact(rel, line, "ses", op, via, _service_cred(services, "ses"))
        return _mail_fact(rel, line, tr, op, via, _service_cred(services, tr), resource=_service_domain(services, tr))
    if default or mailers:
        for m in re.finditer(r"""\bMail::(send|raw|html|plain|queue|later|sendNow|to|cc|bcc|mailer)\s*\(""", text):
            name = m.group(1)
            mailer = default
            if name == "mailer":
                lit = re.match(r"""\s*['\"]([\w\-]+)['\"]""", text[m.end():m.end() + 80])
                mailer = lit.group(1) if lit else default
            if name in ("to", "cc", "bcc", "mailer"):
                stmt = text[m.end():m.end() + 500].split(";", 1)[0]
                sm = re.search(r"""->(send|queue|later|sendNow)\s*\(""", stmt)
                if not sm:
                    continue
                name = sm.group(1)
            if not mailer or mailer in IGNORED_MAILERS:
                continue
            f = transport_fact(_line_at(text, m.start()), mailer, name, "laravel-mail")
            if f:
                facts.append(f)
    for m in re.finditer(r"""function\s+via\s*\([^)]*\)[^{]*\{""", text):
        body = _brace(text, m.end() - 1)
        chans = set(re.findall(r"""['\"](\w+)['\"]""", body)) | {c.lower().replace("channel", "") for c in re.findall(r"""(\w+Channel)::class""", body)}
        line = _line_at(text, m.start())
        if "mail" in chans and default and default not in IGNORED_MAILERS:
            f = transport_fact(line, default, "notification", "laravel-notification")
            if f:
                facts.append(f)
        for chan, prov in (("vonage", "vonage"), ("nexmo", "vonage"), ("twilio", "twilio")):
            if chan in chans:
                via = "laravel-notification-channels/twilio" if prov == "twilio" else "laravel/vonage-notification-channel"
                f = _mail_fact(rel, line, prov, "notification", via, _service_cred(services, prov if prov != "vonage" or "vonage" in {k.split(".")[1] for k in services} else "nexmo"))
                if f:
                    facts.append(f)
    return facts


def _php_ctor_vars(text: str, ctor: str):
    """(var, args, pos) for `$var = new X(...)` / `$this->var = Factory::create(...)`; ctor is the regex up to the '('."""
    for m in re.finditer(r"""(?:\$this->|\$)(\w+)\s*=\s*(?:new\s+)?""" + ctor + r"""\s*\(""", text):
        yield m.group(1), _args_at(text, m.end() - 1), m.start()


def _php_calls(text: str, var: str, suffix: str):
    for m in re.finditer(r"""(?:\$this->|\$)%s\s*%s""" % (re.escape(var), suffix), text):
        yield m


def _collect_mail_php(rel: str, text: str, ctx: dict) -> list:
    facts = []
    services = ctx["services"]
    if not re.search(r"SendGrid|Mailgun|Postmark|Resend|Twilio|Vonage|Nexmo", text):
        return facts

    def add(provider, line, op, via, cred, resource=None):
        f = _mail_fact(rel, line, provider, op, via, cred, resource)
        if f:
            facts.append(f)
    for var, args, _p in _php_ctor_vars(text, r"""\\?(?:SendGrid\\)?SendGrid"""):
        cred = _cred_expr(args.split(",")[0], text, services)
        for m in _php_calls(text, var, r"->send\("):
            add("sendgrid", _line_at(text, m.start()), "send", "sendgrid/sendgrid", cred)
    for var, args, _p in _php_ctor_vars(text, r"""\\?(?:Mailgun\\)?Mailgun::create"""):
        cred = _cred_expr(args.split(",")[0], text, services)
        for m in _php_calls(text, var, r"->messages\(\)->send\("):
            add("mailgun", _line_at(text, m.start()), "messages.send", "mailgun/mailgun-php", cred,
                _res_expr(_args_at(text, m.end() - 1).split(",")[0]))
    if "Mailgun" in text:
        known = {p for v, a, p in _php_ctor_vars(text, r"""\\?(?:Mailgun\\)?Mailgun::create""")}
        if not known:
            for m in re.finditer(r"""->messages\(\)->send\(""", text):
                add("mailgun", _line_at(text, m.start()), "messages.send", "mailgun/mailgun-php", ("none", []),
                    _res_expr(_args_at(text, m.end() - 1).split(",")[0]))
    for var, args, _p in _php_ctor_vars(text, r"""\\?(?:Postmark\\)?PostmarkClient"""):
        cred = _cred_expr(args.split(",")[0], text, services)
        for m in _php_calls(text, var, r"->(sendEmail|sendEmailBatch|sendEmailWithTemplate)\("):
            add("postmark", _line_at(text, m.start()), m.group(1), "wildbit/postmark-php", cred)
    for var, args, _p in _php_ctor_vars(text, r"""\\?Resend::client"""):
        cred = _cred_expr(args.split(",")[0], text, services)
        for m in _php_calls(text, var, r"->(emails|batch)->send\("):
            add("resend", _line_at(text, m.start()), f"{m.group(1)}.send", "resend/resend-php", cred)
    for var, args, _p in _php_ctor_vars(text, r"""\\?(?:Twilio\\Rest\\)?Client"""):
        if "Twilio" not in text:
            continue
        parts = _split_args(args)
        cred = _merge_cred(*(_cred_expr(a, text, services) for a in parts[:2]))
        for m in _php_calls(text, var, r"->(messages|calls)->create\("):
            add("twilio", _line_at(text, m.start()), f"{m.group(1)}.create", "twilio/sdk", cred)
    for var, args, _p in _php_ctor_vars(text, r"""\\?(?:Vonage\\)?Client"""):
        if "Vonage" not in text:
            continue
        cred = _merge_cred(*(_cred_expr(a, text, services) for a in _split_args(args + "," + _args_around(text, _p))))
        for m in _php_calls(text, var, r"->(sms|messages)\(\)->send\("):
            add("vonage", _line_at(text, m.start()), f"{m.group(1)}.send", "vonage/client", cred)
    return facts


def _args_around(text: str, pos: int) -> str:
    """The Credentials\\Basic / Keypair arguments a Vonage client is built from (a few lines around its assignment)."""
    w = text[max(0, pos - 300):pos + 400]
    m = re.search(r"""Credentials\\(?:Basic|Keypair)\s*\(""", w)
    return _args_at(w, m.end() - 1) if m else ""


def _split_args(args: str) -> list[str]:
    out, depth, cur, q = [], 0, "", None
    for c in args:
        if q:
            cur += c
            q = None if c == q else q
            continue
        if c in "'\"":
            q = c
        elif c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
        if c == "," and depth == 0:
            out.append(cur)
            cur = ""
            continue
        cur += c
    if cur.strip():
        out.append(cur)
    return out


_TS_LHS = r"""(?:(?:const|let|var)\s+(\w+)|this\.(\w+)|(\w+))\s*(?::[^=\n]+)?=\s*(?:await\s+)?"""


def _ts_ctor_vars(text: str, ctor: str):
    for m in re.finditer(_TS_LHS + ctor + r"""\s*\(""", text):
        var = m.group(1) or m.group(2) or m.group(3)
        yield var, _args_at(text, m.end() - 1), m.start()


def _ts_calls(text: str, var: str, suffix: str):
    for m in re.finditer(r"""(?:\bthis\.)?\b%s\s*%s""" % (re.escape(var), suffix), text):
        yield m


def _ts_names(text: str, mod: str) -> set[str]:
    names = set()
    for m in re.finditer(r"""import\s+(?:\*\s+as\s+)?(\w+)\s*(?:,\s*\{[^}]*\})?\s*from\s*['\"]%s['\"]""" % mod, text):
        names.add(m.group(1))
    for m in re.finditer(r"""(?:const|let|var)\s+(\w+)\s*=\s*require\(\s*['\"]%s['\"]\s*\)""" % mod, text):
        names.add(m.group(1))
    return names


def _ts_named(text: str, mod: str) -> set[str]:
    out = set()
    for m in re.finditer(r"""import\s+(?:\w+\s*,\s*)?\{([^}]*)\}\s*from\s*['\"]%s['\"]""" % mod, text):
        for part in m.group(1).split(","):
            bits = part.strip().split(" as ")
            if bits[0].strip():
                out.add(bits[-1].strip())
    for m in re.finditer(r"""(?:const|let|var)\s+\{([^}]*)\}\s*=\s*require\(\s*['\"]%s['\"]\s*\)""" % mod, text):
        for part in m.group(1).split(","):
            bits = part.strip().split(":")
            if bits[0].strip():
                out.add(bits[-1].strip())
    return out


def _collect_mail_ts(rel: str, text: str, ctx: dict) -> list:
    facts = []
    if not re.search(r"@sendgrid/mail|mailgun\.js|['\"]postmark['\"]|['\"]resend['\"]|['\"]twilio['\"]|@vonage/server-sdk", text):
        return facts

    def add(provider, pos, op, via, cred, resource=None):
        f = _mail_fact(rel, _line_at(text, pos), provider, op, via, cred, resource)
        if f:
            facts.append(f)
    # @sendgrid/mail: sgMail.setApiKey(key); sgMail.send(msg)
    sg = _ts_names(text, r"@sendgrid/mail")
    for var, _a, _p in _ts_ctor_vars(text, r"new\s+(?:\w+\.)?MailService"):
        sg.add(var)
    for n in sg:
        creds = [_cred_expr(_args_at(text, m.end() - 1), text) for m in _ts_calls(text, n, r"\.setApiKey\(")]
        cred = _merge_cred(*creds)
        for m in _ts_calls(text, n, r"\.(send|sendMultiple)\("):
            add("sendgrid", m.start(), m.group(1), "@sendgrid/mail", cred)
    # mailgun.js: new Mailgun(formData).client({ username, key }); mg.messages.create(domain, data)
    if "mailgun.js" in text:
        for var, args, _p in _ts_ctor_vars(text, r"(?:new\s+\w+\s*\([^)]*\)|\w+)\.client"):
            cred = _cred_expr(args, text)
            for m in _ts_calls(text, var, r"\.messages\.create\("):
                add("mailgun", m.start(), "messages.create", "mailgun.js", cred, _res_expr(_args_at(text, m.end() - 1).split(",")[0]))
    # postmark: new ServerClient(token).sendEmail
    if re.search(r"['\"]postmark['\"]", text):
        for var, args, _p in _ts_ctor_vars(text, r"new\s+(?:\w+\.)?ServerClient"):
            cred = _cred_expr(args.split(",")[0], text)
            for m in _ts_calls(text, var, r"\.(sendEmail|sendEmailBatch|sendEmailWithTemplate)\("):
                add("postmark", m.start(), m.group(1), "postmark", cred)
    # resend: new Resend(key).emails.send
    if re.search(r"['\"]resend['\"]", text):
        for var, args, _p in _ts_ctor_vars(text, r"new\s+(?:\w+\.)?Resend"):
            cred = _cred_expr(args.split(",")[0], text)
            for m in _ts_calls(text, var, r"\.(emails|batch)\.send\("):
                add("resend", m.start(), f"{m.group(1)}.send", "resend", cred)
    # twilio: twilio(sid, token) / new Twilio(sid, token)
    if re.search(r"['\"]twilio['\"]", text):
        names = _ts_names(text, r"twilio")
        ctors = [r"(?:%s)" % "|".join(re.escape(n) for n in names)] if names else []
        ctors += [r"new\s+(?:\w+\.)?Twilio"]
        for ctor in ctors:
            for var, args, _p in _ts_ctor_vars(text, ctor):
                parts = _split_args(args)
                cred = _merge_cred(*(_cred_expr(a, text) for a in parts[:2]))
                for m in _ts_calls(text, var, r"\.(messages|calls|verify)\b[\w.]*\.create\("):
                    add("twilio", m.start(), f"{m.group(1)}.create", "twilio", cred)
    # @vonage/server-sdk: new Vonage({ apiKey, apiSecret }).sms.send
    if "@vonage/server-sdk" in text:
        for var, args, _p in _ts_ctor_vars(text, r"new\s+(?:\w+\.)?Vonage"):
            cred = _cred_expr(args, text)
            for m in _ts_calls(text, var, r"\.(sms|messages)\.send\("):
                add("vonage", m.start(), f"{m.group(1)}.send", "@vonage/server-sdk", cred)
    return facts


_PY_LHS = r"""(?:self\.)?(\w+)\s*(?::[^=\n]+)?=\s*"""


def _py_ctor_vars(text: str, ctor: str):
    for m in re.finditer(_PY_LHS + ctor + r"""\s*\(""", text):
        yield m.group(1), _args_at(text, m.end() - 1), m.start()


def _py_arg(args: str, *names: str) -> str:
    """The keyword argument value (first matching name) or the first positional argument."""
    parts = _split_args(args)
    for p in parts:
        k, eq, v = p.partition("=")
        if eq and k.strip() in names:
            return v
    for p in parts:
        if "=" not in p.split("(")[0]:
            return p
    return ""


def _collect_mail_py(rel: str, text: str, ctx: dict) -> list:
    facts = []
    st = ctx["py_settings"]
    if not re.search(r"sendgrid|twilio|postmarker|\bresend\b|vonage|django\.core\.mail", text, re.I):
        return facts

    def add(provider, pos, op, via, cred, resource=None):
        f = _mail_fact(rel, _line_at(text, pos), provider, op, via, cred, resource)
        if f:
            facts.append(f)
    if "sendgrid" in text:
        for var, args, _p in _py_ctor_vars(text, r"(?:\w+\.)?SendGridAPIClient"):
            cred = _cred_expr(_py_arg(args, "api_key"), text, None, st)
            for m in _ts_calls(text, var, r"\.send\("):
                add("sendgrid", m.start(), "send", "sendgrid", cred)
    if "twilio" in text:
        for var, args, _p in _py_ctor_vars(text, r"(?:twilio\.rest\.)?Client"):
            parts = _split_args(args)
            cred = _merge_cred(*(_cred_expr(a.partition("=")[2] or a, text, None, st) for a in parts[:2]))
            for m in _ts_calls(text, var, r"\.(messages|calls)\.create\("):
                add("twilio", m.start(), f"{m.group(1)}.create", "twilio", cred)
    if "postmarker" in text:
        for var, args, _p in _py_ctor_vars(text, r"(?:\w+\.)?PostmarkClient"):
            cred = _cred_expr(_py_arg(args, "server_token", "token"), text, None, st)
            for m in _ts_calls(text, var, r"\.emails\.(send|send_batch)\("):
                add("postmark", m.start(), f"emails.{m.group(1)}", "postmarker", cred)
    if re.search(r"^\s*(?:import resend|from resend)", text, re.M):
        am = re.search(r"""\bresend\.api_key\s*=\s*([^\n]+)""", text)
        cred = _cred_expr(am.group(1), text, None, st) if am else ("none", [])
        for m in re.finditer(r"""\bresend\.(Emails|Batch)\.send\(""", text):
            add("resend", m.start(), f"{m.group(1).lower()}.send", "resend", cred)
    if "vonage" in text:
        creds = []
        for _v, args, _p in _py_ctor_vars(text, r"vonage\.Client"):
            creds += [_cred_expr(_py_arg(args, k), text, None, st) for k in ("key", "api_key")]
            creds += [_cred_expr(_py_arg(args, k), text, None, st) for k in ("secret", "api_secret")] if "secret" in args else []
        cred = _merge_cred(*creds)
        for m in re.finditer(r"""\.(sms\.send_message|send_message|messages\.send)\(""", text):
            add("vonage", m.start(), m.group(1), "vonage", cred)
    if "django.core.mail" in text and ctx.get("anymail"):
        spec = ctx["anymail"]
        names = {"send_mail", "send_mass_mail", "mail_admins", "mail_managers"}
        for n in names:
            for m in re.finditer(r"""(?<![\w.])%s\(""" % n, text):
                if re.search(r"def\s+%s" % n, text[max(0, m.start() - 8):m.start() + len(n) + 1]):
                    continue
                f = _mail_fact(rel, _line_at(text, m.start()), spec["provider"], n, "django-anymail", spec["cred"], spec.get("resource"))
                if f:
                    facts.append(f)
        for m in re.finditer(r"""\b(?:EmailMessage|EmailMultiAlternatives)\s*\(""", text):
            end = m.end() - 1 + len(_args_at(text, m.end() - 1)) + 2
            hit = re.match(r"""\s*\.send\(""", text[end:])
            var = None
            if not hit:
                lm = re.search(r"""(\w+)\s*=\s*$""", text[max(0, m.start() - 60):m.start()])
                var = lm.group(1) if lm else None
            sites = [m.start()] if hit else [x.start() for x in _ts_calls(text, var, r"\.send\(")] if var else []
            for pos in sites:
                f = _mail_fact(rel, _line_at(text, pos), spec["provider"], "send", "django-anymail", spec["cred"], spec.get("resource"))
                if f:
                    facts.append(f)
    return facts


def _anymail_settings(text: str, st: dict):
    """EMAIL_BACKEND = 'anymail.backends.<esp>.EmailBackend' + the ANYMAIL dict -> {provider, cred, resource}."""
    m = re.search(r"""^EMAIL_BACKEND\s*=\s*['\"]anymail\.backends\.(\w+)\.EmailBackend['\"]""", text, re.M)
    if not m or m.group(1) not in ANYMAIL_ESP:
        return None
    esp, prov = m.group(1), ANYMAIL_ESP[m.group(1)]
    am = re.search(r"""^ANYMAIL\s*=\s*\{""", text, re.M)
    body = _brace(text, am.end() - 1) if am else ""
    cred, resource = ("none", []), None
    for km in re.finditer(r"""['\"]([A-Z][A-Z0-9_]+)['\"]\s*:\s*([^\n]+)""", body):
        key, expr = km.group(1), km.group(2)
        if not key.startswith(esp.upper().replace("AMAZON_", "AMAZON_")) and not key.startswith(prov.upper().replace("-", "_")):
            continue
        if re.search(r"DOMAIN", key):
            resource = _res_expr(expr)
        elif SECRET_FIELD.search(key):
            cred = _merge_cred(cred, _cred_expr(expr, text, None, st))
    return {"provider": prov, "cred": cred, "resource": resource}


def collect(root) -> list:
    facts = []
    storage: dict = {}
    disks: dict = {}
    default = None
    py_chunks, php_chunks, ts_mail = [], [], []
    for rel, text in _files(root):
        ext = os.path.splitext(rel)[1].lower()
        if ext == ".py":
            py_chunks.append((rel, text))
        elif ext in (".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx"):
            facts += _collect_ts(rel, text)
            ts_mail.append((rel, text))
        elif ext == ".php":
            php_chunks.append((rel, text))
    for rel, text in php_chunks:
        if "filesystems" in rel or ("'disks'" in text or '"disks"' in text):
            found, dflt = _php_disks(text)
            disks.update(found)
            if dflt:
                default = dflt
    for rel, text in php_chunks:
        more, default = _collect_php(rel, text, disks, default)
        facts += more
    for rel, text in py_chunks:
        if any(k in text for k in ("STORAGES", "DEFAULT_FILE_STORAGE")):
            _collect_py(rel, text, storage)
    for rel, text in py_chunks:
        facts += [f for f in _collect_py(rel, text, storage) if f["via"] != "django-storages" or storage]
    mail_ctx = {"services": {}, "mailers": {}, "mail_default": None, "py_settings": {}, "anymail": None}
    for rel, text in php_chunks:
        if rel.endswith("config/services.php"):
            mail_ctx["services"].update(_services_php(text))
        elif rel.endswith("config/mail.php"):
            mail_ctx["mailers"], mail_ctx["mail_default"], mail_env = _php_mailers(text)
            mail_ctx["mail_env"] = mail_env
    if mail_ctx["mailers"] and mail_ctx.get("mail_env"):
        mail_ctx["mail_default"] = _env_file_value(root, mail_ctx["mail_env"]) or mail_ctx["mail_default"]
    if mail_ctx["mail_default"] is None:
        api = [m for m, t in mail_ctx["mailers"].items() if t in LARAVEL_MAIL_TRANSPORT]
        if len(api) == 1:
            mail_ctx["mail_default"] = api[0]
    for rel, text in py_chunks:
        for m in re.finditer(r"^([A-Z][A-Z0-9_]+)\s*=\s*([^\n]+)", text, re.M):
            c = _cred_expr(m.group(2), "", None, None)
            if c[0] != "none":
                mail_ctx["py_settings"][m.group(1)] = c
    for rel, text in py_chunks:
        if "EMAIL_BACKEND" in text:
            mail_ctx["anymail"] = _anymail_settings(text, mail_ctx["py_settings"]) or mail_ctx["anymail"]
    for rel, text in php_chunks:
        facts += _collect_laravel_mail(rel, text, mail_ctx) if not rel.endswith("config/mail.php") else []
        facts += _collect_mail_php(rel, text, mail_ctx)
    for rel, text in py_chunks:
        facts += _collect_mail_py(rel, text, mail_ctx)
    for rel, text in ts_mail:
        facts += _collect_mail_ts(rel, text, mail_ctx)
    # dedupe
    seen = set()
    out = []
    for f in facts:
        key = (f["file"], f["line"], f["protocol"], f["target"], f["op"])
        if key in seen:
            continue
        seen.add(key)
        out.append(f)
    return out


def _owner(builder, rel: str, line: int) -> str | None:
    best = None
    best_span = None
    for n in builder.nodes.values():
        if (n.file or "").replace("\\", "/") != rel or n.kind not in ("function", "method"):
            continue
        lo = n.line or 0
        hi = n.end_line or lo
        if lo <= line <= hi:
            span = hi - lo
            if best is None or span < best_span:
                best, best_span = n, span
    if best is not None:
        return best.id
    for n in builder.nodes.values():
        if (n.file or "").replace("\\", "/") == rel and n.kind in ("module", "script"):
            return n.id
    return None


def _skip_src(builder, src: str) -> bool:
    if src.startswith("test:"):
        return True
    n = builder.nodes.get(src)
    if n is None:
        return True
    if is_test_node(n) or (n.attrs or {}).get("test_only"):
        return True
    return False


def _http_same_host(builder, src: str, endpoint: str | None) -> bool:
    """True when this caller already CONNECTS_TO external:http(s) for the SDK endpoint host."""
    if not endpoint:
        return False
    d = parse_dsn(endpoint)
    if not d or d["protocol"] not in ("http", "https"):
        return False
    from .external import target_of
    hid = f"external:{d['protocol']}:{target_of(d['host'], d['port'])}"
    return any(e.kind == "CONNECTS_TO" and e.src == src and e.dst == hid for e in builder.edges.values())


def attach_sdk(builder, root, node, cred, st) -> None:
    try:
        facts = collect(root)
    except OSError:
        return
    # llm_calls already attached external:llm:<provider> from the same function: do not add a second edge
    llm_done = set()
    for e in builder.edges.values():
        if e.kind == "CONNECTS_TO" and isinstance(e.dst, str) and e.dst.startswith("external:llm:"):
            llm_done.add((e.src, e.dst))
    for f in facts:
        src = _owner(builder, f["file"], f["line"])
        if not src or _skip_src(builder, src):
            continue
        if _http_same_host(builder, src, f.get("endpoint")) and f["protocol"] in ("http", "https"):
            continue
        attrs = {
            "library": f["via"], "address_source": f["address_source"],
            "resource": f.get("resource"), "auth": f["auth"],
        }
        if f.get("endpoint"):
            attrs["endpoint"] = f["endpoint"]
        if f["auth"] == "ambient":
            attrs["credential_source"] = "ambient"
        if f.get("resource") and f["protocol"] == "saas":
            attrs["resource"] = f["resource"]
        nid = node(f["protocol"], f["target"], attrs, f["confidence"])
        if (src, nid) in llm_done and f["protocol"] == "llm":
            continue
        # auth explicit wins over ambient when any caller passes keys
        n = builder.nodes[nid]
        if f["auth"] == "explicit":
            n.attrs["auth"] = "explicit"
            if f.get("literal_credential"):
                n.attrs["credential_literal"] = True
                if n.attrs.get("credential_source") != "env":
                    n.attrs["credential_source"] = "literal"
                    n.attrs["credential_at"] = f"{f['file']}:{f['line']}"
            else:
                n.attrs["credential_source"] = "env"
        elif f["auth"] == "unknown":
            if n.attrs.get("auth") not in ("explicit",):
                n.attrs["auth"] = "unknown"
        else:
            n.attrs.setdefault("auth", "ambient")
            n.attrs.setdefault("credential_source", "ambient")
        extra = {"via": f["via"], "op": f["op"], "auth": f["auth"]}
        if f.get("literal_credential"):
            extra["literal_credential"] = True
        if f.get("resource") and f["protocol"] == "saas":
            extra["resource"] = f["resource"]
        builder.add_edge(src, nid, "CONNECTS_TO", f["file"], f["line"], f["confidence"], **extra)
        st["connects"] += 1
        if f.get("resource_env"):
            eid = f"env:{f['resource_env']}"
            if eid in builder.nodes:
                builder.add_edge(nid, eid, "CONFIGURED_BY", None, None, f["confidence"])
        if f["auth"] == "explicit" and f.get("creds"):
            if f.get("ensure_env"):
                for k in f["creds"]:
                    builder.add_node("env", k, lang="env")
            cred(nid, f["creds"])
            n.attrs["credential_source"] = "env"
            n.attrs["credential_at"] = f"env:{f['creds'][0]}"
