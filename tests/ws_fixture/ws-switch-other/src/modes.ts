import http from 'http'
import { WebSocketServer } from 'ws'

// a `case` on something other than the pathname does not name the server's path
export function attachModes(server: http.Server) {
  const wssMode = new WebSocketServer({ noServer: true })
  function onMode(req: http.IncomingMessage, socket: any, head: Buffer) {
    const mode = String(req.headers['x-mode'] || '')
    switch (mode) {
      case '/legacy':
        wssMode.handleUpgrade(req, socket, head, (ws) => wssMode.emit('connection', ws, req))
        break
    }
  }
  server.on('upgrade', onMode)
}
