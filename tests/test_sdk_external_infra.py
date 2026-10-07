"""Push, Firebase, Kubernetes / Docker API clients, key management and MessageBird / Plivo as external systems
(#42 part 3b), in PHP / TS / Python."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from cg_code_graph.core.store import GraphStore  # noqa: E402
from cg_code_graph.external import external  # noqa: E402
from cg_code_graph.indexer import index_project  # noqa: E402

FX = ROOT / "tests" / "external_fixture"


def _index(tmp_path, name):
    db = tmp_path / f"{name}.db"
    index_project(FX / name, db, name)
    return GraphStore(db)


def _ext(st):
    return {r["id"]: json.loads(r["attrs"] or "{}") for r in st.q("SELECT id, attrs FROM nodes WHERE kind='external'")}


def _ct(st):
    return [(r["src"], r["dst"], json.loads(r["attrs"] or "{}")) for r in st.q("SELECT src, dst, attrs FROM edges WHERE kind='CONNECTS_TO'")]


def _creds(st):
    return {(r["src"], r["dst"]) for r in st.q("SELECT src, dst FROM edges WHERE kind='CREDENTIAL_FROM'")}


def _edge(ct, dst, caller):
    return next(a for s, d, a in ct if d == dst and s.endswith(caller))


def _callers(ct, dst):
    return {s for s, d, _a in ct if d == dst}


# ------------------------------------------------------------------ push
def test_push_ts(tmp_path):
    st = _index(tmp_path, "push-ts")
    e, ct, creds = _ext(st), _ct(st), _creds(st)
    for sid in ("gcp:fcm", "saas:apns", "saas:webpush", "saas:expo-push"):
        assert f"external:{sid}" in e
    fcm = _edge(ct, "external:gcp:fcm", "notifyShipped")
    assert fcm["op"] == "send" and fcm["via"] == "firebase-admin" and fcm["auth"] == "ambient"
    assert e["external:gcp:fcm"]["credential_source"] == "ambient"
    assert _edge(ct, "external:saas:apns", "notifyIos")["auth"] == "explicit"
    assert ("external:saas:apns", "env:APNS_KEY_ID") in creds
    lit = _edge(ct, "external:saas:apns", "notifyIosLiteral")
    assert lit["auth"] == "explicit" and lit["credential_file"] == "certs/AuthKey_BOOKSTORE.p8"
    assert "literal_credential" not in lit
    assert _edge(ct, "external:saas:webpush", "notifyBrowser")["op"] == "sendNotification"
    assert ("external:saas:webpush", "env:VAPID_PRIVATE_KEY") in creds
    assert ("external:saas:expo-push", "env:EXPO_ACCESS_TOKEN") in creds
    assert not any(s.endswith("push.test.ts") or "test" in s.split("#")[0].split("/")[-1] for s, _d, _a in ct), "test callers are skipped"


def test_push_py(tmp_path):
    st = _index(tmp_path, "push-py")
    e, ct, creds = _ext(st), _ct(st), _creds(st)
    assert _edge(ct, "external:gcp:fcm", "notify_shipped")["auth"] == "ambient"
    ios = _edge(ct, "external:saas:apns", "notify_ios")
    assert ios["via"] == "apns2" and ios["op"] == "send_notification" and ios["auth"] == "explicit"
    assert ("external:saas:apns", "env:APNS_KEY_PATH") in creds
    assert _edge(ct, "external:saas:apns", "notify_ios_async")["via"] == "aioapns"
    assert _edge(ct, "external:saas:webpush", "notify_browser")["auth"] == "explicit"
    assert ("external:saas:webpush", "env:VAPID_PRIVATE_KEY") in creds
    assert _edge(ct, "external:saas:webpush", "notify_browser_unkeyed")["auth"] == "unknown"
    assert not any("push_cases" in s for s, _d, _a in ct)


def test_push_php(tmp_path):
    st = _index(tmp_path, "push-php")
    ct, creds = _ct(st), _creds(st)
    notif = [a for s, d, a in ct if d == "external:gcp:fcm" and a["op"] == "notification"]
    assert notif and notif[0]["via"] == "laravel-notification-channels/fcm"
    assert ("external:gcp:fcm", "env:FIREBASE_CREDENTIALS") in creds
    apn = [a for s, d, a in ct if d == "external:saas:apns" and a["op"] == "notification"]
    assert apn and apn[0]["via"] == "laravel-notification-channels/apn"
    assert ("external:saas:apns", "env:APN_KEY_ID") in creds
    assert _edge(ct, "external:saas:apns", "ios")["via"] == "edamov/pushok"
    assert ("external:saas:apns", "env:APNS_KEY_PATH") in creds
    wp = _edge(ct, "external:saas:webpush", "browser")
    assert wp["via"] == "minishlink/web-push" and wp["op"] == "sendOneNotification"
    assert ("external:saas:webpush", "env:VAPID_PRIVATE_KEY") in creds
    assert _edge(ct, "external:gcp:fcm", "android")["op"] == "send"


# ------------------------------------------------------------------ Firebase
def test_firebase_ts(tmp_path):
    st = _index(tmp_path, "firebase-ts")
    e, ct, creds = _ext(st), _ct(st), _creds(st)
    orders = _edge(ct, "external:gcp:firestore:orders", "listOrders")
    assert orders["op"] == "get" and orders["auth"] == "explicit"
    assert e["external:gcp:firestore:orders"]["resource"] == "orders"
    assert ("external:gcp:firestore:orders", "env:FIREBASE_SERVICE_ACCOUNT") in creds
    assert "external:gcp:firestore:env:REVIEWS_COLLECTION" in e
    assert _edge(ct, "external:gcp:firestore", "checkout")["op"] == "runTransaction"
    assert _edge(ct, "external:gcp:firebase-auth", "whoIs")["op"] == "verifyIdToken"
    assert _edge(ct, "external:gcp:firebase-rtdb", "stock")["op"] == "set"
    assert "external:gcs:bookstore-prod.appspot.com" in e and "external:gcs:bookstore-exports" in e
    amb = _edge(ct, "external:gcp:firestore:authors", "listAuthors")
    assert amb["auth"] == "ambient" and e["external:gcp:firestore:authors"]["credential_source"] == "ambient"
    assert not any(s == "external:gcp:firestore:authors" for s, _d in creds)
    carts = _edge(ct, "external:gcp:firestore:carts", "listCarts")
    assert carts["auth"] == "explicit" and carts["credential_file"] == "./secrets/service-account.json"
    assert e["external:gcp:firestore:carts"]["credential_source"] == "file"
    assert _edge(ct, "external:gcp:fcm", "notifyLowStock")["op"] == "send"
    # browser Firebase client SDKs are not server systems
    assert not any("browserClient" in s for s in {s for s, _d, _a in ct})
    assert "external:gcp:firestore:catalog" not in e


def test_firebase_py(tmp_path):
    st = _index(tmp_path, "firebase-py")
    e, ct, creds = _ext(st), _ct(st), _creds(st)
    o = _edge(ct, "external:gcp:firestore:orders", "list_orders")
    assert o["via"] == "firebase-admin" and o["auth"] == "explicit"
    assert ("external:gcp:firestore:orders", "env:GOOGLE_APPLICATION_CREDENTIALS") in creds
    assert "external:gcp:firestore:env:REVIEWS_COLLECTION" in e
    assert _edge(ct, "external:gcp:firebase-auth", "who_is")["op"] == "verify_id_token"
    assert _edge(ct, "external:gcp:fcm", "push")["op"] == "send"
    assert _edge(ct, "external:gcp:firebase-rtdb", "stock")["op"] == "set"
    assert "external:gcs:bookstore-prod.appspot.com" in e
    assert _edge(ct, "external:gcp:firestore:authors", "list_authors")["auth"] == "ambient"
    assert _edge(ct, "external:gcp:firebase-auth", "drop_user")["credential_file"] == "secrets/service-account.json"


def test_firebase_php(tmp_path):
    st = _index(tmp_path, "firebase-php")
    e, ct, creds = _ext(st), _ct(st), _creds(st)
    o = _edge(ct, "external:gcp:firestore:orders", "orders")
    assert o["via"] == "kreait/firebase-php" and o["auth"] == "explicit"
    assert ("external:gcp:firestore:orders", "env:FIREBASE_CREDENTIALS") in creds
    assert _edge(ct, "external:gcp:firebase-auth", "whoIs")["op"] == "verifyIdToken"
    rt = _edge(ct, "external:gcp:firebase-rtdb", "stock")
    assert rt["auth"] == "explicit" and rt["credential_file"] == "firebase/service-account.json"
    assert _edge(ct, "external:gcp:firestore:reviews", "ambientReviews")["auth"] == "ambient"
    assert e["external:gcp:firestore:reviews"]["credential_source"] == "ambient"


# ------------------------------------------------------------------ Kubernetes
def test_k8s_ts(tmp_path):
    st = _index(tmp_path, "k8s-ts")
    e, ct, creds = _ext(st), _ct(st), _creds(st)
    job = _edge(ct, "external:k8s:batch-v1", "runExport")
    assert job["op"] == "createNamespacedJob" and job["namespace"] == "exports" and job["auth"] == "ambient"
    assert e["external:k8s:batch-v1"]["protocol"] == "k8s"
    assert _edge(ct, "external:k8s:core-v1", "listWorkers")["namespace"] == "workers"
    scale = _edge(ct, "external:k8s:apps-v1", "scale")
    assert scale["auth"] == "explicit" and scale["namespace"] == "shop"
    assert ("external:k8s:apps-v1", "env:KUBECONFIG") in creds
    local = _edge(ct, "external:k8s:core-v1", "listNamespaces")
    assert local["credential_file"] == "/home/ops/.kube/bookstore-config"
    assert _edge(ct, "external:k8s:networking-v1", "defaultCtx")["auth"] == "ambient"


def test_k8s_py(tmp_path):
    st = _index(tmp_path, "k8s-py")
    ct, creds = _ct(st), _creds(st)
    job = _edge(ct, "external:k8s:batch-v1", "run_export")
    assert job["op"] == "create_namespaced_job" and job["namespace"] == "exports" and job["auth"] == "ambient"
    assert _edge(ct, "external:k8s:core-v1", "list_workers")["namespace"] == "workers"
    assert _edge(ct, "external:k8s:apps-v1", "scale")["auth"] == "explicit"
    assert ("external:k8s:apps-v1", "env:KUBECONFIG") in creds
    assert _edge(ct, "external:k8s:core-v1", "local_namespaces")["credential_file"] == "/home/ops/.kube/bookstore-config"


def test_k8s_php(tmp_path):
    st = _index(tmp_path, "k8s-php")
    ct = _ct(st)
    assert _edge(ct, "external:k8s:core-v1", "inCluster")["via"] == "renoki-co/php-k8s"
    assert {a["op"] for _s, d, a in ct if d == "external:k8s:core-v1"} == {"pod.create", "getAllPods"}
    assert all(a["auth"] == "ambient" for _s, _d, a in ct)


# ------------------------------------------------------------------ Docker
def _docker_checks(st, calls):
    e, ct, creds = _ext(st), _ct(st), _creds(st)
    assert _edge(ct, "external:docker:/var/run/docker.sock", calls["default"][0])["op"] == calls["default"][1]
    assert e["external:docker:/var/run/docker.sock"]["auth"] == "ambient"
    assert "tls" not in e["external:docker:/var/run/docker.sock"]
    assert "external:docker:/run/bookstore/docker.sock" in e
    plain = e["external:docker:10.20.0.5:2375"]
    assert plain["tls"] is False and plain["host"] == "10.20.0.5" and plain["port"] == 2375
    assert _edge(ct, "external:docker:10.20.0.5:2375", calls["plain"][0])["op"] == calls["plain"][1]
    secure = e["external:docker:build.bookstore.example:2376"]
    assert secure["tls"] is True and secure["auth"] == "explicit"
    assert ("external:docker:build.bookstore.example:2376", "env:DOCKER_CERT") in creds
    assert "external:docker:env:DOCKER_HOST" in e
    return e


def test_docker_ts(tmp_path):
    st = _index(tmp_path, "docker-ts")
    _docker_checks(st, {"default": ("listLocal", "listContainers"), "plain": ("build", "createContainer")})
    assert _edge(_ct(st), "external:docker:/run/bookstore/docker.sock", "startReport")["op"] == "getContainer.start"


def test_docker_py(tmp_path):
    st = _index(tmp_path, "docker-py")
    _docker_checks(st, {"default": ("run_report", "containers.run"), "plain": ("remote_list", "containers.list")})
    assert _edge(_ct(st), "external:docker:/run/bookstore/docker.sock", "sock_images")["op"] == "images.pull"


def test_docker_php(tmp_path):
    st = _index(tmp_path, "docker-php")
    ct = _ct(st)
    assert _edge(ct, "external:docker:/var/run/docker.sock", "run")["op"] == "containerList"


# ------------------------------------------------------------------ key management
def test_kms_and_vaults(tmp_path):
    st = _index(tmp_path, "kms")
    e, ct, creds = _ext(st), _ct(st), _creds(st)
    alias = "external:aws:kms:alias/bookstore-orders"
    assert e[alias]["resource"] == "alias/bookstore-orders"
    assert _edge(ct, alias, "sealLiteral")["auth"] == "ambient" and _edge(ct, alias, "sealLiteral")["op"] == "Encrypt"
    assert _edge(ct, alias, "openExplicit")["auth"] == "explicit"
    assert (alias, "env:AWS_ACCESS_KEY_ID") in creds
    assert _edge(ct, alias, "seal_literal")["op"] == "encrypt"
    assert "external:aws:kms:env:ORDERS_KMS_KEY_ID" in e
    assert _edge(ct, "external:aws:kms:env:ORDERS_KMS_KEY_ID", "sealEnv")["auth"] == "ambient"
    assert _edge(ct, "external:aws:kms:env:ORDERS_KMS_KEY_ID", "open")["auth"] == "explicit"
    assert "external:aws:kms:1234abcd-12ab-34cd-56ef-1234567890ab" in e, "an ARN is cut to the key id"
    assert _edge(ct, "external:aws:kms:alias/bookstore-orders", "seal")["via"] == "aws-sdk-php"
    sec = "external:gcp:secretmanager:db-password"
    assert _edge(ct, sec, "dbPassword")["op"] == "accessSecretVersion"
    assert (sec, "env:GOOGLE_APPLICATION_CREDENTIALS") in creds
    assert "external:gcp:secretmanager:env:SECRET_RESOURCE" in e
    assert e["external:gcp:secretmanager:mail-token"]["auth"] == "ambient"
    assert _edge(ct, "external:gcp:kms", "seal")["op"] == "encrypt"
    kv = "external:azure:keyvault:bookstore-vault.vault.azure.net"
    assert _edge(ct, kv, "apiKey")["auth"] == "ambient" and _edge(ct, kv, "api_key")["op"] == "get_secret"
    kv_env = "external:azure:keyvault:env:KEY_VAULT_URL"
    assert _edge(ct, kv_env, "explicit")["auth"] == "explicit"
    assert (kv_env, "env:AZURE_CLIENT_SECRET") in creds


# ------------------------------------------------------------------ MessageBird / Plivo
def test_messagebird_plivo(tmp_path):
    st = _index(tmp_path, "sms-extra")
    e, ct, creds = _ext(st), _ct(st), _creds(st)
    assert e["external:saas:messagebird"]["protocol"] == "saas" and e["external:saas:plivo"]["protocol"] == "saas"
    assert _edge(ct, "external:saas:messagebird", "viaMessagebird")["op"] == "messages.create"
    assert _edge(ct, "external:saas:messagebird", "via_messagebird")["op"] == "message_create"
    assert ("external:saas:messagebird", "env:MESSAGEBIRD_API_KEY") in creds
    assert ("external:saas:plivo", "env:PLIVO_AUTH_ID") in creds
    lit = _edge(ct, "external:saas:plivo", "via_plivo")
    assert lit["literal_credential"] is True and e["external:saas:plivo"]["credential_literal"] is True
    for caller, via in (("viaPlivo", "plivo"), ("via_plivo", "plivo"), ("phpPlivo", "plivo/plivo-php")):
        assert _edge(ct, "external:saas:plivo", caller)["via"] == via
    assert _edge(ct, "external:saas:messagebird", "phpMessagebird")["via"] == "messagebird/php-rest-api"
    assert not any(d == "external:saas:plivo" and s.endswith("Messagebird") for s, d, _a in ct), "calls stay in their method"


# ------------------------------------------------------------------ CLI filters and impact
def test_cg_external_protocols_and_impact(tmp_path):
    st = _index(tmp_path, "k8s-ts")
    res = external(st, protocol="k8s")
    assert {s["id"] for s in res["systems"]} == {
        "external:k8s:apps-v1", "external:k8s:batch-v1", "external:k8s:core-v1", "external:k8s:networking-v1"}
    st = _index(tmp_path, "docker-ts")
    res = external(st, protocol="docker")
    assert "external:docker:/var/run/docker.sock" in {s["id"] for s in res["systems"]}
    assert [s["id"] for s in external(st, protocol="docker", tls_off=True)["systems"]] == ["external:docker:10.20.0.5:2375"]
    st = _index(tmp_path, "kms")
    assert any(s["id"].startswith("external:gcp:secretmanager") for s in external(st, protocol="gcp")["systems"])
    assert any(s["id"].startswith("external:azure:keyvault") for s in external(st, protocol="azure")["systems"])
    st = _index(tmp_path, "push-ts")
    assert "external:gcp:fcm" in {s["id"] for s in external(st, protocol="gcp")["systems"]}
    assert "external:saas:apns" in {s["id"] for s in external(st, protocol="saas")["systems"]}
    from cg_code_graph import query as Q
    imp = Q.impact(st, "external:gcp:fcm")
    assert "route:POST /orders/{id}/ship" in {x["id"] for x in imp["entry_points"]}
