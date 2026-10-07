"""Mail and SMS APIs as external systems (#42 part 3a): SendGrid, Mailgun, Postmark, Resend, Twilio, Vonage, SES
in PHP / TS / Python, Laravel mailers and notification channels, Django anymail."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from cg_code_graph.core.store import GraphStore  # noqa: E402
from cg_code_graph.external import external  # noqa: E402
from cg_code_graph.indexer import index_project  # noqa: E402

FX = ROOT / "tests" / "external_fixture"
PROVIDERS = ["sendgrid", "mailgun", "postmark", "resend", "twilio", "vonage"]


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


def _edge(ct, provider, caller):
    return next(a for s, d, a in ct if d == f"external:saas:{provider}" and s.endswith(caller))


def _check_matrix(st, callers, ops, literal):
    e, ct, creds = _ext(st), _ct(st), _creds(st)
    for p in PROVIDERS:
        assert e[f"external:saas:{p}"]["protocol"] == "saas"
    for p in PROVIDERS:
        a = _edge(ct, p, callers[p])
        assert a["op"] == ops[p]
    for p in ("sendgrid", "resend", "twilio", "vonage"):
        assert _edge(ct, p, callers[p])["auth"] == "explicit"
    assert ("external:saas:sendgrid", "env:SENDGRID_API_KEY") in creds
    assert ("external:saas:resend", "env:RESEND_API_KEY") in creds
    assert ("external:saas:twilio", "env:TWILIO_ACCOUNT_SID") in creds or ("external:saas:twilio", "env:TWILIO_SID") in creds
    assert any(s == "external:saas:vonage" for s, _ in creds)
    lit = _edge(ct, literal[0], literal[1])
    assert lit["auth"] == "explicit" and lit["literal_credential"] is True
    assert e[f"external:saas:{literal[0]}"]["credential_literal"] is True
    assert not any(s == f"external:saas:{literal[0]}" for s, _ in creds)


def test_mail_sms_php(tmp_path):
    st = _index(tmp_path, "mail-sms-php")
    c = {"sendgrid": "viaSendgrid", "mailgun": "viaMailgun", "postmark": "viaPostmark", "resend": "viaResend",
         "twilio": "sms", "vonage": "vonageSms"}
    o = {"sendgrid": "send", "mailgun": "messages.send", "postmark": "sendEmail", "resend": "emails.send",
         "twilio": "messages.create", "vonage": "sms.send"}
    _check_matrix(st, c, o, ("mailgun", "viaMailgun"))
    ct = _ct(st)
    mg = _edge(ct, "mailgun", "viaMailgun")
    assert mg["resource"] == "mg.bookstore.example"
    assert _edge(ct, "sendgrid", "noKey")["auth"] == "unknown"
    assert _edge(ct, "sendgrid", "viaSendgrid")["via"] == "sendgrid/sendgrid"
    assert not any("MailersTest" in s for s, _d, _a in ct), "test callers are skipped"


def test_mail_sms_ts(tmp_path):
    st = _index(tmp_path, "mail-sms-ts")
    c = {"sendgrid": "viaSendgrid", "mailgun": "viaMailgun", "postmark": "viaPostmark", "resend": "viaResend",
         "twilio": "sms", "vonage": "vonageSms"}
    o = {"sendgrid": "send", "mailgun": "messages.create", "postmark": "sendEmail", "resend": "emails.send",
         "twilio": "messages.create", "vonage": "sms.send"}
    ct = _ct(st)
    assert _edge(ct, "mailgun", "viaMailgun")["resource"] == "mg.bookstore.example"
    assert ("external:saas:mailgun", "env:MAILGUN_API_KEY") in _creds(st)
    _check_matrix(st, c, o, ("postmark", "viaPostmark"))
    assert _edge(ct, "resend", "unknownKey")["auth"] == "unknown"


def test_mail_sms_py(tmp_path):
    st = _index(tmp_path, "mail-sms-py")
    e, ct, creds = _ext(st), _ct(st), _creds(st)
    assert {f"external:saas:{p}" for p in PROVIDERS if p != "mailgun"} <= set(e)
    assert _edge(ct, "sendgrid", "via_sendgrid")["op"] == "send"
    assert _edge(ct, "twilio", "sms")["op"] == "messages.create"
    assert _edge(ct, "resend", "via_resend")["auth"] == "explicit"
    assert _edge(ct, "sendgrid", "no_key")["auth"] == "unknown"
    pm = _edge(ct, "postmark", "via_postmark")
    assert pm["literal_credential"] is True
    assert e["external:saas:postmark"]["credential_literal"] is True
    assert ("external:saas:sendgrid", "env:SENDGRID_API_KEY") in creds
    assert ("external:saas:twilio", "env:TWILIO_ACCOUNT_SID") in creds
    assert ("external:saas:vonage", "env:VONAGE_API_KEY") in creds
    assert ("external:saas:resend", "env:RESEND_API_KEY") in creds
    assert "external:http" not in " ".join(e)


def test_laravel_mailers_and_channels(tmp_path):
    st = _index(tmp_path, "laravel-mail")
    e, ct, creds = _ext(st), _ct(st), _creds(st)
    # the default mailer (.env.example MAIL_MAILER=mailgun) is one shared node for every Mail:: caller
    mg = [s for s, d, a in ct if d == "external:saas:mailgun"]
    assert any(s.endswith("OrderController::ship") for s in mg)
    assert any(s.endswith("OrderController::plain") for s in mg)
    assert any(s.endswith("OrderPacked::via") for s in mg)
    assert e["external:saas:mailgun"]["resource"] == "env:MAILGUN_DOMAIN"
    assert ("external:saas:mailgun", "env:MAILGUN_SECRET") in creds
    # Mail::mailer('postmark') picks the named mailer; smtp is ignored (SMTP is #44)
    assert any(s.endswith("OrderController::receipt") for s, d, _a in ct if d == "external:saas:postmark")
    assert not any(s.endswith("OrderController::viaSmtp") for s, _d, _a in ct)
    assert e["external:aws:ses"]["auth"] == "ambient"
    assert any(s.endswith("OrderController::archive") for s, d, _a in ct if d == "external:aws:ses")
    # notification channels
    assert any(s.endswith("OrderPacked::via") and a["op"] == "notification" for s, d, a in ct if d == "external:saas:vonage")
    assert ("external:saas:vonage", "env:VONAGE_KEY") in creds
    # config('services.sendgrid.key') resolves through config/services.php
    sg = _edge(ct, "sendgrid", "Bulk::blast")
    assert sg["auth"] == "explicit"
    assert ("external:saas:sendgrid", "env:SENDGRID_API_KEY") in creds
    assert not any(d.startswith("external:smtp") for _s, d, _a in ct)


def test_django_anymail(tmp_path):
    st = _index(tmp_path, "django-anymail")
    ct = _ct(st)
    hits = {s.split(".")[-1]: a for s, d, a in ct if d == "external:saas:sendgrid"}
    assert hits["notify"]["op"] == "send_mail" and hits["notify"]["via"] == "django-anymail"
    assert hits["receipt"]["op"] == "send"
    assert ("external:saas:sendgrid", "env:SENDGRID_API_KEY") in _creds(st)


def test_cg_external_protocol_saas_and_impact(tmp_path):
    st = _index(tmp_path, "laravel-mail")
    res = external(st, protocol="saas")
    ids = {s["id"] for s in res["systems"]}
    assert {"external:saas:mailgun", "external:saas:postmark", "external:saas:vonage", "external:saas:sendgrid"} <= ids
    assert "external:aws:ses" not in ids
    from cg_code_graph import query as Q
    imp = Q.impact(st, "external:saas:mailgun")
    assert "route:POST /orders/{id}/ship" in {e["id"] for e in imp["entry_points"]}


def test_ses_v2_and_nodemailer_ts(tmp_path):
    st = _index(tmp_path, "mail-sms-ts")
    ct = _ct(st)
    ses = {s.replace("#", ".").rsplit(".", 1)[-1]: a for s, d, a in ct if d == "external:aws:ses"}
    assert ses["sesv2Send"]["op"] == "SendEmail" and ses["sesv2Send"]["via"] == "@aws-sdk/client-sesv2"
    assert ses["sesv2Identity"]["op"] == "CreateEmailIdentity"
    assert ses["nodemailerSes"]["op"] == "sendMail" and ses["nodemailerSes"]["via"] == "nodemailer"
    assert _edge(ct, "resend", "nodemailerResend")["via"] == "nodemailer"
    assert _edge(ct, "resend", "nodemailerSmtpResend")["op"] == "sendMail"
    assert ("external:saas:resend", "env:RESEND_API_KEY") in _creds(st)
    assert not any(s.endswith("nodemailerPlainSmtp") and d.startswith(("external:saas:", "external:aws:")) for s, d, _a in ct)


def test_ses_v2_py(tmp_path):
    st = _index(tmp_path, "mail-sms-py")
    ses = {s.replace("#", ".").rsplit(".", 1)[-1]: a for s, d, a in _ct(st) if d == "external:aws:ses"}
    assert ses["send_v2"]["op"] == "send_email"
    assert ses["verify_domain"]["op"] == "create_email_identity"
    assert not any(d == "external:aws:sesv2" for _s, d, _a in _ct(st))
