"""Cloud and SaaS SDK calls as external nodes (#42 part 2).

Not HTTP hosts (`external:http(s):…` is part 1). A client construction plus an operation names the system:

  external:s3:<bucket-or-env>          boto3 / @aws-sdk/client-s3 / Laravel s3 disks / django-storages
  external:gcs:<bucket-or-env>         google.cloud.storage / @google-cloud/storage
  external:azure-blob:<container-or-env>
  external:aws:<service>[:resource]    sqs, secretsmanager, dynamodb, ses
  external:saas:stripe
  external:llm:<provider>              openai / anthropic when the call is not already an attrs.llm_calls edge

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
_TEST_PATH = re.compile(r"(^|/)(tests?|__tests__|spec)(/|$)|\.(test|spec)\.[cm]?[jt]sx?$|Test\.php$|(^|/)test_.*\.py$")
_CRED_KW = ("aws_access_key_id", "aws_secret_access_key", "aws_session_token", "accessKeyId", "secretAccessKey", "sessionToken")


def _rel(root, path) -> str:
    return os.path.relpath(path, root).replace(os.sep, "/")


def _files(root):
    for dp, dns, fns in os.walk(root):
        dns[:] = [d for d in dns if d not in SKIP and not d.startswith(".")]
        for fn in fns:
            ext = os.path.splitext(fn)[1].lower()
            if ext not in EXTS and fn != "filesystems.php":
                continue
            p = os.path.join(dp, fn)
            try:
                if os.path.getsize(p) > 1_500_000:
                    continue
                text = open(p, errors="replace").read()
            except OSError:
                continue
            if fn == "filesystems.php" or MARK.search(text):
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


def collect(root) -> list:
    facts = []
    storage: dict = {}
    disks: dict = {}
    default = None
    py_chunks, php_chunks = [], []
    for rel, text in _files(root):
        ext = os.path.splitext(rel)[1].lower()
        if ext == ".py":
            py_chunks.append((rel, text))
        elif ext in (".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx"):
            facts += _collect_ts(rel, text)
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
        nid = node(f["protocol"], f["target"], attrs, f["confidence"])
        if (src, nid) in llm_done and f["protocol"] == "llm":
            continue
        # auth explicit wins over ambient when any caller passes keys
        n = builder.nodes[nid]
        if f["auth"] == "explicit":
            n.attrs["auth"] = "explicit"
            n.attrs["credential_source"] = "env"
        else:
            n.attrs.setdefault("auth", "ambient")
            n.attrs.setdefault("credential_source", "ambient")
        extra = {"via": f["via"], "op": f["op"], "auth": n.attrs.get("auth") or f["auth"]}
        builder.add_edge(src, nid, "CONNECTS_TO", f["file"], f["line"], f["confidence"], **extra)
        st["connects"] += 1
        if f.get("resource_env"):
            eid = f"env:{f['resource_env']}"
            if eid in builder.nodes:
                builder.add_edge(nid, eid, "CONFIGURED_BY", None, None, f["confidence"])
        if f["auth"] == "explicit" and f.get("creds"):
            cred(nid, f["creds"])
            n.attrs["credential_source"] = "env"
            n.attrs["credential_at"] = f"env:{f['creds'][0]}"
