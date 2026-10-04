import paho.mqtt.client as mqtt
import nats


def on_state(client, userdata, msg):
    return msg.payload


def run_mqtt():
    client = mqtt.Client()
    client.on_message = on_state
    client.connect("broker", 1883)
    client.subscribe([("devices/+/state", 0), ("alerts/#", 1)])
    client.publish("devices/lamp-1/state", "on")


async def stock_handler(msg):
    await msg.respond(b"42")


async def run_nats():
    nc = await nats.connect("nats://nats:4222")
    await nc.subscribe("stock.check.*", queue="stock", cb=stock_handler)
