import * as net from 'net'

const SOCKET_PATH = '/tmp/node-ipc.sock'

export function startIpc() {
  const server = net.createServer((conn) => conn.end('ok'))
  server.listen(SOCKET_PATH)
  return server
}

export function startPort() {
  const server = net.createServer((conn) => conn.end('ok'))
  server.listen(8080)
}

export function startPipe() {
  const server = net.createServer((conn) => conn.end('ok'))
  server.listen('\\\\.\\pipe\\node-ipc')
}
