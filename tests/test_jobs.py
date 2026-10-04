"""Job queues as protocol endpoints (#36): codegraph/jobs.py over tests/jobs_fixture (a Flask shop enqueuing Celery
tasks by name and RQ jobs by import path, a Celery billing worker with task_routes and a Procfile, a mailer with RQ
and Dramatiq workers in docker-compose, a Django shop whose Celery job nodes get endpoint twins, a Bull / BullMQ
producer and worker, a Laravel app with Horizon, a Symfony Messenger app), linked by cg link."""
import json
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from codegraph.core.store import GraphStore  # noqa: E402
from codegraph.indexer import index_project  # noqa: E402
from codegraph.jobs import Scan, decorated, CELERY_DECO  # noqa: E402
from codegraph.link import link  # noqa: E402
from codegraph.protocols.view import protocols  # noqa: E402

FX = ROOT / "tests" / "jobs_fixture"
J, Q = "endpoint:job:", "endpoint:queue:"


@pytest.fixture(scope="module")
def dbs(tmp_path_factory):
    d = tmp_path_factory.mktemp("jobs")
    out = {}
    for r in ("shop-web", "billing-worker", "mailer", "django-shop", "bull-api", "bull-worker", "laravel-app", "symfony-shop"):
        out[r + "-stats"] = index_project(FX / r, d / f"{r}.db", r)
        out[r] = d / f"{r}.db"
    for be, fe in (("billing-worker", "shop-web"), ("mailer", "shop-web"), ("bull-worker", "bull-api")):
        out[f"link-{be}"] = d / f"link-{be}.db"
        out[f"link-{be}-res"] = link(str(out[be]), str(out[fe]), str(out[f"link-{be}"]), backend_name=be, frontend_name=fe)
    return out


def _edges(db, kind):
    con = sqlite3.connect(db)
    return {(r[0], r[1]): (r[2], json.loads(r[3] or "{}")) for r in con.execute(
        "select src, dst, confidence, attrs from edges where kind=?", (kind,))}


def _attrs(db, nid):
    r = sqlite3.connect(db).execute("select attrs, entry_kind from nodes where id=?", (nid,)).fetchone()
    return (json.loads(r[0]), r[1]) if r else (None, None)


def _view(db, proto):
    return {e["name"]: e for e in protocols(GraphStore(db), protocol=proto)["endpoints"]}


def test_decorators_span_lines_and_nest():
    src = '@app.task(\n    name="a.b",\n    description=_("x (y, z)"),\n)\n# note\n@other\ndef f(): pass\n@shared_task\ndef g(): pass\n'
    got = [(d, a.strip().split(",")[0], f) for d, a, f, _ in decorated(src, CELERY_DECO)]
    assert got == [("app.task", 'name="a.b"', "f"), ("shared_task", "", "g")]


def test_celery_tasks_routes_and_workers(dbs):
    db = dbs["billing-worker"]
    rb = _edges(db, "RECEIVED_BY") | _edges(db, "QUEUE_ROUTES")
    assert (J + "celery:billing.charge", "function:billing.tasks.charge") in rb
    assert (J + "celery:billing.export.csv", "function:billing.tasks.export_csv") in rb
    assert (J + "celery:billing.tasks.nightly_report", "function:billing.tasks.nightly_report") in rb   # no name=: qualname
    assert (Q + "celery/billing", "function:billing.tasks.charge") in rb                  # queue= on the decorator
    assert (Q + "celery/exports", "function:billing.tasks.export_csv") in rb              # task_routes glob
    a, ek = _attrs(db, J + "celery:billing.charge")
    assert a["queue"] == "billing" and a["processes"] == ["worker"] and ek == "message_handler"
    assert _attrs(db, Q + "celery/celery")[0]["consumers"] == ["worker"]                   # -Q billing,celery
    assert "consumers" not in _attrs(db, Q + "celery/exports")[0]
    st = _edges(db, "SENDS_TO")
    assert st[("function:billing.tasks.close_day", J + "celery:billing.export.csv")][1]["role"] == "enqueue"
    assert ("function:billing.tasks.close_day", Q + "celery/exports") in st               # the task's routed queue
    assert not any(k[1] == J + "celery:billing.charge" for k in st)                        # the docstring .delay
    v = _view(db, "queue")
    assert v["celery/exports"]["checks"] == ["no_consumer"]
    assert v["celery/billing"]["checks"] == ["no_sender"]                                  # the shop sends it (cg link)


def test_name_based_sends(dbs):
    st = _edges(dbs["shop-web"], "SENDS_TO")
    c, a = st[("function:shop.views.create_order", J + "celery:billing.charge")]
    assert c == "resolved" and a["how"] == "send_task" and a["queue"] == "billing"        # settings env default
    assert ("function:shop.views.create_order", Q + "celery/billing") in st
    assert ("function:shop.views.create_order", J + "rq:mailer.jobs.send_receipt") in st
    assert ("function:shop.views.create_order", Q + "rq/emails") in st                    # Queue("emails") variable
    c, _a = st[("function:shop.views.signup", J + "rq:mailer.jobs.send_{json}")]          # f-string -> template
    assert c == "heuristic"
    assert dbs["shop-web-stats"]["jobs"]["celery_sends"] == 2


def test_dramatiq_and_rq_workers(dbs):
    db = dbs["mailer"]
    rb = _edges(db, "RECEIVED_BY") | _edges(db, "QUEUE_ROUTES")
    assert (J + "dramatiq:send_digest", "function:mailer.actors.send_digest") in rb
    assert (J + "dramatiq:mailer.bounce", "function:mailer.actors.handle_bounce") in rb   # actor_name=
    st = _edges(db, "SENDS_TO")
    assert st[("function:mailer.actors.daily", J + "dramatiq:send_digest")][1]["how"] == ".send()"
    assert ("function:mailer.actors.daily", Q + "dramatiq/bounces") in st
    assert _attrs(db, Q + "dramatiq/digest")[0]["consumers"] == ["digest-worker"]       # compose service, -Q digest
    a = _attrs(db, Q + "rq/emails")[0]                       # ["rq", "worker", "--url", "redis://..", "emails"]
    assert a["consumers"] == ["mailer-worker"] and a["served"] == "worker process"
    assert _view(db, "queue")["dramatiq/bounces"]["checks"] == ["no_consumer"]


def test_django_twins_keep_dispatches(dbs):
    db = dbs["django-shop"]
    a, ek = _attrs(db, J + "celery:shop.tasks.send_invoice")
    assert a["job_node"] == "job:shop.tasks.send_invoice" and a["queue"] == "invoices" and ek is None
    assert _attrs(db, "job:shop.tasks.send_invoice")[1] == "queue_job"                   # entry tagging unchanged
    assert ("function:shop.views.place_order", "job:shop.tasks.send_invoice") in _edges(db, "DISPATCHES")
    st = _edges(db, "SENDS_TO")
    assert not any(k[1] == J + "celery:shop.tasks.send_invoice" for k in st)               # no duplicate of DISPATCHES
    assert ("function:shop.views.place_order", Q + "celery/invoices") in st                # CELERY_TASK_ROUTES
    assert ("function:shop.views.place_order", J + "celery:billing.charge") in st         # unknown name: SENDS_TO
    v = protocols(GraphStore(db), protocol="job")["endpoints"]
    ids = {e["id"] for e in v}
    assert "job:shop.tasks.send_invoice" in ids and J + "celery:shop.tasks.send_invoice" not in ids   # merged
    one = next(e for e in v if e["id"] == "job:shop.tasks.send_invoice")
    assert one["protocol"] == "celery" and one["senders"] and one["receivers"]


def test_cg_link_celery(dbs):
    db = dbs["link-billing-worker"]
    v = _view(db, "job")
    assert v["celery:billing.charge"]["linked"] and v["celery:billing.charge"]["checks"] == []
    assert [s["fn"] for s in v["celery:billing.charge"]["senders"]] == ["function:shop.views.create_order"]
    assert v["celery:billing.refund"]["checks"] == ["no_receiver"]                         # no worker registers it
    q = _view(db, "queue")
    assert q["celery/billing"]["linked"] and q["celery/exports"]["checks"] == ["no_consumer"]


def test_cg_link_rq_by_import_path(dbs):
    db = dbs["link-mailer"]
    assert dbs["link-mailer-res"]["stats"]["protocols"]["job"]["rq_import_paths"] == 2
    rb = _edges(db, "RECEIVED_BY") | _edges(db, "QUEUE_ROUTES")
    c, a = rb[(J + "rq:mailer.jobs.send_receipt", "function:mailer.jobs.send_receipt")]
    assert c == "heuristic" and a["how"] == "import path (cg link)"
    assert (J + "rq:mailer.jobs.send_welcome", "function:mailer.jobs.send_welcome") in rb  # the template's candidates
    assert not any(k[1] == "function:mailer.jobs._render" for k in rb)
    v = _view(db, "job")
    assert v["rq:mailer.jobs.send_receipt"]["linked"]
    me = _edges(db, "MATCHES_ENDPOINT")
    assert (J + "rq:mailer.jobs.send_{json}", J + "rq:mailer.jobs.send_welcome") in me
    q = _view(db, "queue")
    assert q["rq/emails"]["linked"] and q["rq/emails"]["checks"] == []                     # consumed by mailer-worker


def test_proc_names():
    assert Scan._proc_name("Procfile", ["web: x", "worker: rq worker a"], 1) == "worker"
    assert Scan._proc_name("deploy/docker-compose.yml", ["services:", "  w1:", "    command: x"], 2) == "w1"
    assert Scan._proc_name("conf/sv.conf", ["[program:jobs]", "command=rq worker"], 1) == "jobs"
    assert Scan._proc_name("ops/netbox-rq.service", ["ExecStart=x"], 0) == "netbox-rq.service"


def test_bull_queues_factories_and_link(dbs):
    st = _edges(dbs["bull-api"], "SENDS_TO")
    assert any(k[1] == J + "bull:emails:welcome" for k in st)                              # enum queue name, named add
    assert any(k[1] == Q + "bull/digest" for k in st)                                       # createQueue() factory getter
    assert dbs["bull-api-stats"]["jobs"]["bull_factory_queues"] == 1
    rb = _edges(dbs["bull-worker"], "RECEIVED_BY") | _edges(dbs["bull-worker"], "QUEUE_ROUTES")
    assert (J + "bull:emails:{name}", "function:src/worker.ts#sendEmail") in rb               # new Worker(q, handler)
    assert (Q + "bull/digest", "function:src/worker.ts#startWorkers") in rb                   # inline process(fn)
    v = _view(dbs["link-bull-worker"], "job")
    assert v["bull:emails:welcome"]["linked"] and v["bull:emails:welcome"]["checks"] == []
    q = _view(dbs["link-bull-worker"], "queue")
    assert q["bull/digest"]["linked"] and q["bull/emails"]["linked"]


def test_laravel_queues_and_horizon(dbs):
    db = dbs["laravel-app"]
    a, ek = _attrs(db, J + "laravel:App\\Jobs\\ProcessPodcast")
    assert a["job_node"] == "job:App\\Jobs\\ProcessPodcast" and a["queue"] == "podcasts" and ek is None
    assert a["processes"] == ["horizon:supervisor-1"]
    st = _edges(db, "SENDS_TO")
    store = "method:App\\Http\\Controllers\\PodcastController::store"
    assert st[(store, Q + "laravel/podcasts")][1]["how"] == "job $queue"                    # public $queue = 'podcasts'
    assert st[(store, Q + "laravel/mail")][1]["task"] == "App\\Jobs\\SendDigest"         # ->onQueue('mail')
    assert (store, Q + "laravel/podcasts") in st and not any(k[1].startswith(J) for k in st)   # DISPATCHES not repeated
    v = _view(db, "queue")
    assert v["laravel/mail"]["checks"] == ["no_consumer"] and v["laravel/podcasts"]["checks"] == []
    jobs = {e["id"]: e for e in protocols(GraphStore(db), protocol="job")["endpoints"]}
    assert "job:App\\Jobs\\ProcessPodcast" in jobs and jobs["job:App\\Jobs\\ProcessPodcast"]["protocol"] == "laravel-queue"


def test_symfony_messenger(dbs):
    db = dbs["symfony-shop"]
    rb = _edges(db, "RECEIVED_BY") | _edges(db, "QUEUE_ROUTES")
    assert (J + "messenger:App\\Message\\SendInvoice", "method:App\\MessageHandler\\SendInvoiceHandler::__invoke") in rb
    assert (J + "messenger:App\\Message\\ResizeImage", "method:App\\MessageHandler\\ImageHandlers::resize") in rb
    assert (Q + "messenger/async", "method:App\\MessageHandler\\SendInvoiceHandler::__invoke") in rb   # routing
    st = _edges(db, "SENDS_TO")
    create = "method:App\\Controller\\OrderController::create"
    assert {k[1] for k in st if k[0] == create} == {
        J + "messenger:App\\Message\\SendInvoice", J + "messenger:App\\Message\\ResizeImage",
        J + "messenger:App\\Message\\AuditLog", Q + "messenger/async", Q + "messenger/images", Q + "messenger/audit"}
    assert _attrs(db, Q + "messenger/images")[0]["consumers"] == ["worker"]                # messenger:consume async images
    jv, qv = _view(db, "job"), _view(db, "queue")
    assert jv["messenger:App\\Message\\AuditLog"]["checks"] == ["no_receiver"]
    assert qv["messenger/audit"]["checks"] == ["no_receiver", "no_consumer"] and qv["messenger/async"]["checks"] == []

