import { Bonjour } from "bonjour-service";
import { Client } from "node-osc";
import { Client as SsdpClient } from "node-ssdp";

const bonjour = new Bonjour();

export function findPrinters() {
  bonjour.find({ type: "printer" }, (svc) => svc.name);
}

export function publishWeb() {
  bonjour.publish({ name: "web", type: "http", port: 3000 });
}

export function oscVolume() {
  const c = new Client("127.0.0.1", 9000);
  c.send("/mixer/volume", 0.8);
}

export function findLights() {
  new SsdpClient().search("urn:schemas-upnp-org:device:BinaryLight:1");
}
