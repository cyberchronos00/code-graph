import * as mqtt from "mqtt";
import { connect } from "nats";

const client = mqtt.connect("mqtt://broker:1883");

export function setLight(deviceId: string, on: boolean) {
  client.publish(`devices/${deviceId}/set`, JSON.stringify({ on }));
}

export function watchStates() {
  client.subscribe("devices/+/state");
  client.on("message", onDeviceState);
}

function onDeviceState(topic: string, payload: Buffer) {
  return [topic, payload.toString()];
}

export async function askStock(sku: string) {
  const nc = await connect({ servers: "nats://nats:4222" });
  return nc.request(`stock.check.${sku}`, new Uint8Array());
}
