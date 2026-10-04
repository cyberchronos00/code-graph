import * as net from "net";
import * as dgram from "dgram";

const CACHE_PORT = 6380;

export function startCache() {
  const server = net.createServer(onClient);
  server.listen(process.env.CACHE_PORT || CACHE_PORT, "127.0.0.1");
}

function onClient(socket: net.Socket) {
  socket.end();
}

export function startMetrics() {
  const sock = dgram.createSocket("udp4");
  sock.on("message", onMetric);
  sock.bind(8125);
}

function onMetric(msg: Buffer) {
  return msg.length;
}
