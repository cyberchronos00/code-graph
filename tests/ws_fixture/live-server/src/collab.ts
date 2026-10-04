import http from 'http'
import WebSocket from 'ws'

export function attachCollab(server: http.Server) {
  const path = '/collab'
  const wss = new WebSocket.Server({ noServer: true })

  server.on('upgrade', function onUpgrade(req, socket, head) {
    if (req.url?.startsWith(path)) {
      wss.handleUpgrade(req, socket, head, (client) => {
        client.on('message', (m) => client.send(m))
      })
      return
    }
    socket.destroy()
  })
}
