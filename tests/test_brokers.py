"""Message brokers and pub/sub as protocol endpoints (#35): codegraph/brokers.py over tests/brokers_fixture (a TS
orders API producing to Kafka, a RabbitMQ topic exchange, Redis, MQTT and NATS; a Python fulfilment worker consuming
them with confluent-kafka, pika, redis-py, paho-mqtt and nats-py; a Laravel notifier with Redis pub/sub and
php-amqplib; part 2: a Kotlin / Spring billing service and a Rust ledger with lapin and rdkafka), linked by cg link."""
import json
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from codegraph.core.store import GraphStore  # noqa: E402
from codegraph.indexer import index_project  # noqa: E402
from codegraph.link import link  # noqa: E402
from codegraph.protocols import matchers as M  # noqa: E402
from codegraph.protocols.view import protocols  # noqa: E402

FX = ROOT / "tests" / "brokers_fixture"
E = "endpoint:"


@pytest.fixture(scope="module")
def dbs(tmp_path_factory):
    d = tmp_path_factory.mktemp("brokers")
    out = {}
    for r in ("orders-api", "fulfil-worker", "notify-php", "billing-kt", "ledger-rs"):
        out[r + "-stats"] = index_project(FX / r, d / f"{r}.db", r)
        out[r] = d / f"{r}.db"
    out["link"] = d / "link.db"
    out["link-res"] = link(str(out["fulfil-worker"]), str(out["orders-api"]), str(out["link"]),
                           backend_name="fulfil-worker", frontend_name="orders-api")
    out["link2"] = d / "link2.db"
    link(str(out["ledger-rs"]), str(out["billing-kt"]), str(out["link2"]), backend_name="ledger-rs",
         frontend_name="billing-kt")
    return out


def _edges(db, kind):
    con = sqlite3.connect(db)
    return {(r[0], r[1]): (r[2], json.loads(r[3] or "{}")) for r in con.execute(
        "select src, dst, confidence, attrs from edges where kind=?", (kind,))}


def _view(db, proto):
    return {e["name"]: e for e in protocols(GraphStore(db), protocol=proto)["endpoints"]}


def test_amqp_matcher():
    assert M.amqp("shop.events/order.eu.created", "shop.events/order.eu.*") is not None
    assert M.amqp("shop.events/order.us.created", "shop.events/order.eu.*") is None
    assert M.amqp("other/order.eu.created", "shop.events/#") is None                 # exchange must be equal
    assert M.amqp("logs/", "logs/#") is not None                                     # fanout binding
    assert M.amqp("queue:invoices", "queue:invoices") is not None and M.amqp("queue:a", "queue:b") is None


def test_js_producers(dbs):
    st = _edges(dbs["orders-api"], "SENDS_TO")
    created, cancelled = "function:src/events.ts#orderCreated", "function:src/events.ts#orderCancelled"
    assert st[(created, E + "kafka:orders.created")][0] == "resolved"                  # TS enum member
    assert (cancelled, E + "kafka:orders.cancelled") in st                              # sendBatch topicMessages
    assert st[(cancelled, E + "kafka:audit.orders")][0] == "heuristic"                  # process.env.X ?? "d"
    assert st[(created, E + "amqp:shop.events/order.{region}.created")][1]["exchange"] == "shop.events"
    assert (created, E + "amqp:queue:invoices") in st                                  # sendToQueue: default exchange
    assert (created, E + "redis-pubsub:cache:invalidate") in st
    assert ("function:src/devices.ts#setLight", E + "mqtt:devices/{deviceId}/set") in st   # client = mqtt.connect()
    assert st[("function:src/devices.ts#askStock", E + "nats:stock.check.{sku}")][1]["role"] == "request"
    rb = _edges(dbs["orders-api"], "RECEIVED_BY")
    assert (E + "mqtt:devices/+/state", "function:src/devices.ts#onDeviceState") in rb   # client.on('message', h)


def test_python_consumers(dbs):
    rb = _edges(dbs["fulfil-worker"], "RECEIVED_BY")
    c = "function:fulfil.consumers."
    assert rb[(E + "kafka:orders.created", c + "run_kafka")][1]["group"] == "fulfilment"   # imported constant
    assert rb[(E + "kafka:audit.orders", c + "run_kafka")][0] == "heuristic"             # os.getenv default
    a = rb[(E + "amqp:shop.events/order.eu.*", c + "on_region_order")][1]                # server-named queue binding
    assert a["library"] == "pika"
    assert (E + "amqp:queue:invoices", c + "on_invoice") in rb
    assert (E + "redis-pubsub:cache:invalidate", c + "invalidate") in rb                 # subscribe(**{ch: handler})
    assert (E + "redis-pubsub:metrics:*", c + "run_redis") in rb                         # psubscribe glob
    assert (E + "mqtt:devices/+/state", "function:fulfil.iot.on_state") in rb            # client.on_message = h
    assert (E + "mqtt:alerts/#", "function:fulfil.iot.on_state") in rb
    assert rb[(E + "nats:stock.check.*", "function:fulfil.iot.stock_handler")][1]["group"] == "stock"
    me = _edges(dbs["fulfil-worker"], "MATCHES_ENDPOINT")
    assert (E + "mqtt:devices/lamp-1/state", E + "mqtt:devices/+/state") in me
    v = _view(dbs["fulfil-worker"], "redis-pubsub")
    assert v["fulfil:started"]["checks"] == ["no_receiver"]


def test_php_redis_and_amqp(dbs):
    db = dbs["notify-php"]
    st = _edges(db, "SENDS_TO")
    notify = "method:App\\Services\\Notifier::notify"
    assert (notify, E + "redis-pubsub:notifications") in st                              # Redis::publish(self::CHANNEL)
    assert (notify, E + "amqp:shop.events/order.eu.notified") in st                      # not the commented-out key
    rb = _edges(db, "RECEIVED_BY")
    assert (E + "redis-pubsub:notifications", "method:App\\Console\\Commands\\ListenNotifications::handle") in rb


def test_cross_repo_link(dbs):
    me = _edges(dbs["link"], "MATCHES_ENDPOINT")
    assert me[(E + "amqp:shop.events/order.{region}.created", E + "amqp:shop.events/order.eu.*")][0] == "heuristic"
    assert (E + "nats:stock.check.{sku}", E + "nats:stock.check.*") in me
    k = _view(dbs["link"], "kafka")
    assert k["orders.created"]["linked"] and k["orders.created"]["checks"] == []
    assert k["orders.cancelled"]["checks"] == ["no_receiver"]
    q = _view(dbs["link"], "amqp")
    assert q["queue:invoices"]["linked"]


def test_kotlin_spring_and_clients(dbs):
    db = dbs["billing-kt"]
    rb = _edges(db, "RECEIVED_BY")
    m = "method:billing.Listeners."
    assert rb[(E + "kafka:orders.created", m + "onOrderCreated")][1]["group"] == "billing"     # @KafkaListener
    assert (E + "amqp:shop.events/order.eu.*", m + "onRegionOrder") in rb                    # @RabbitListener bindings
    assert (E + "amqp:queue:invoices", m + "onInvoice") in rb                                # @RabbitListener queues
    st = _edges(db, "SENDS_TO")
    assert st[(m + "onOrderCreated", E + "kafka:billing.invoices")][0] == "resolved"         # object constant
    assert st[("function:billing.publishAudit", E + "amqp:audit/order.{region}.audited")][0] == "heuristic"  # getenv ?: "d"
    assert ("function:billing.publishAudit", E + "nats:stock.check.{region}") in st          # "${x}" template


def test_rust_lapin_and_rdkafka(dbs):
    db = dbs["ledger-rs"]
    st = _edges(db, "SENDS_TO")
    assert st[("function:ledger::post_entry", E + "amqp:ledger/entry.{account}")][1]["library"] == "lapin"  # format!()
    rb = _edges(db, "RECEIVED_BY")
    assert (E + "amqp:ledger/entry.*", "function:ledger::bind_entries") in rb                # queue.name() binding
    assert rb[(E + "kafka:billing.invoices", "function:ledger::consume_invoices")][0] == "heuristic"  # env::var().unwrap_or_else
    me = _edges(db, "MATCHES_ENDPOINT")
    assert (E + "amqp:ledger/entry.{account}", E + "amqp:ledger/entry.*") in me


def test_kotlin_rust_link(dbs):
    k = _view(dbs["link2"], "kafka")
    assert k["billing.invoices"]["linked"]


def test_settings_field_defaults(tmp_path):
    """pydantic-settings / Field snake_case defaults resolve for kafka + amqp (#157)."""
    db = tmp_path / "settings-bus.db"
    index_project(FX / "settings-bus", db, "settings-bus")
    st = _edges(db, "SENDS_TO")
    rb = _edges(db, "RECEIVED_BY")
    assert st[("function:bus.publish.publish_event", E + "kafka:events")][0] == "resolved"
    assert st[("method:bus.publish.EventBus.publish", E + "kafka:events")][0] == "resolved"  # self.settings.*
    assert st[("function:bus.publish.publish_audit", E + "kafka:audit.orders")][0] == "resolved"  # Field(default=)
    assert ("function:bus.publish.publish_job", E + "amqp:queue:jobs") in st
    assert (E + "kafka:events", "function:bus.worker.run_events") in rb
    assert (E + "amqp:queue:jobs", "function:bus.worker.on_job") in rb


def test_helper_param_and_consumer_ctor(tmp_path):
    """routing_key=queue through call-site consts; AIOKafkaConsumer(topic, ...) (#137)."""
    db = tmp_path / "settings-bus2.db"
    index_project(FX / "settings-bus", db, "settings-bus")
    st = _edges(db, "SENDS_TO")
    rb = _edges(db, "RECEIVED_BY")
    assert (E + "kafka:events", "function:bus.worker.run_events") in rb
    assert rb[(E + "kafka:events", "function:bus.worker.run_events")][1].get("group") == "settings-bus-workers"
    assert ("method:bus.tasks.TaskQueue._publish", E + "amqp:queue:jobs.a") in st
    assert ("method:bus.tasks.TaskQueue._publish", E + "amqp:queue:jobs.b") in st
    assert (E + "amqp:queue:jobs.a", "method:bus.tasks.TaskQueue.consume_a") in rb
