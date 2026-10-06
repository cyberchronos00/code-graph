"""Cloud / SaaS SDK systems (#42 part 2): s3, gcs, azure-blob, aws, stripe, llm, Laravel disks, django-storages."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from cg_code_graph.core.store import GraphStore  # noqa: E402
from cg_code_graph.indexer import index_project  # noqa: E402

FX = ROOT / "tests" / "external_fixture"


def _index(tmp_path, name):
    db = tmp_path / f"{name}.db"
    index_project(FX / name, db, name)
    return GraphStore(db)


def _ext(st):
    return {r["id"]: json.loads(r["attrs"] or "{}") for r in st.q("SELECT id, attrs FROM nodes WHERE kind='external'")}


def _ct(st):
    rows = []
    for r in st.q("SELECT src, dst, attrs FROM edges WHERE kind='CONNECTS_TO'"):
        rows.append((r["src"], r["dst"], json.loads(r["attrs"] or "{}")))
    return rows


def test_s3_put_ambient_and_aws_services(tmp_path):
    st = _index(tmp_path, "s3-py")
    e = _ext(st)
    assert e["external:s3:media"]["auth"] == "ambient"
    assert e["external:s3:media"]["credential_source"] == "ambient"
    assert e["external:s3:env:BUCKET"]["resource"] == "BUCKET"
    assert "external:aws:sqs:env:QUEUE_URL" in e
    assert "external:aws:secretsmanager:prod/db" in e
    assert "external:s3:from-test" not in e
    ct = _ct(st)
    up = next(a for s, d, a in ct if d == "external:s3:media")
    assert up["via"] == "boto3" and up["op"] == "put_object" and up["auth"] == "ambient"
    assert any(s.endswith("upload_configured") and d == "external:s3:env:BUCKET" for s, d, _ in ct)
    assert ("external:s3:env:BUCKET", "env:BUCKET") in {
        (r["src"], r["dst"]) for r in st.q("SELECT src, dst FROM edges WHERE kind='CONFIGURED_BY'")
    }
    creds = {(r["src"], r["dst"]) for r in st.q("SELECT src, dst FROM edges WHERE kind='CREDENTIAL_FROM'")}
    assert ("external:s3:env:BUCKET", "env:AWS_ACCESS_KEY_ID") in creds
    assert ("external:s3:env:BUCKET", "env:AWS_SECRET_ACCESS_KEY") in creds
    assert not any(src == "external:s3:media" for src, _dst in creds)


def test_gcs_azure_stripe(tmp_path):
    gcs = _ext(_index(tmp_path, "gcs-py"))
    assert "external:gcs:assets" in gcs and "external:gcs:env:GCS_BUCKET" in gcs
    az = _index(tmp_path, "azure-py")
    assert "external:azure-blob:docs" in _ext(az)
    creds = {(r["src"], r["dst"]) for r in az.q("SELECT src, dst FROM edges WHERE kind='CREDENTIAL_FROM'")}
    assert ("external:azure-blob:docs", "env:AZURE_STORAGE_CONNECTION_STRING") in creds
    pay = _index(tmp_path, "stripe-py")
    e = _ext(pay)
    assert e["external:saas:stripe"]["protocol"] == "saas"
    ct = _ct(pay)
    hit = next(a for s, d, a in ct if d == "external:saas:stripe")
    assert hit["op"] == "Charge.create" and hit["via"] == "stripe"
    assert ("external:saas:stripe", "env:STRIPE_SECRET_KEY") in {
        (r["src"], r["dst"]) for r in pay.q("SELECT src, dst FROM edges WHERE kind='CREDENTIAL_FROM'")
    }


def test_openai_chat_aligns_with_llm(tmp_path):
    st = _index(tmp_path, "openai-chat")
    e = _ext(st)
    assert "external:llm:openai" in e
    ct = [a for _s, d, a in _ct(st) if d == "external:llm:openai"]
    assert ct and all(a.get("op") for a in ct)
    assert ("external:llm:openai", "env:OPENAI_API_KEY") in {
        (r["src"], r["dst"]) for r in st.q("SELECT src, dst FROM edges WHERE kind='CREDENTIAL_FROM'")
    }


def test_laravel_disk_and_sdk(tmp_path):
    st = _index(tmp_path, "laravel-disk")
    e = _ext(st)
    assert "external:s3:env:AWS_BUCKET" in e
    assert "external:s3:media" in e
    ct = _ct(st)
    disk = next(a for s, d, a in ct if d == "external:s3:env:AWS_BUCKET")
    assert disk["via"] == "laravel" and disk["op"] == "put"
    direct = next(a for s, d, a in ct if d == "external:s3:media")
    assert direct["via"] == "aws-sdk-php" and direct["op"] == "putObject" and direct["auth"] == "ambient"
    assert not any(d.startswith("external:http") for _s, d, _a in ct)


def test_django_storages_default(tmp_path):
    st = _index(tmp_path, "django-storages")
    e = _ext(st)
    assert "external:s3:env:AWS_STORAGE_BUCKET_NAME" in e
    ct = _ct(st)
    hit = next(a for s, d, a in ct if d == "external:s3:env:AWS_STORAGE_BUCKET_NAME")
    assert hit["via"] == "django-storages" and hit["op"] == "save"
    assert "save" in next(s for s, d, _a in ct if d == "external:s3:env:AWS_STORAGE_BUCKET_NAME")
