import * as net from "net";
import * as dgram from "dgram";

export function askJobs() {
  const c = net.connect({ port: 7000, host: "jobs.internal" });
  c.end();
}

export function sendMetric(line: string) {
  const s = dgram.createSocket("udp4");
  s.send(line, 8125, "239.1.2.3");
}
