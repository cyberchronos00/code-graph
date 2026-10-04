import http from 'http'
import { WebSocketServer } from 'ws'

export function attachForms(server: http.Server) {
  const wssC = new WebSocketServer({ noServer: true })
  const wssD = new WebSocketServer({ noServer: true })
  const wssE = new WebSocketServer({ noServer: true })
  const wssM = new WebSocketServer({ noServer: true })

  function onSwitch(req: http.IncomingMessage, socket: any, head: Buffer) {
    const p = new URL(req.url || '', 'http://x').pathname
    switch (p) {
      case '/gamma':
        wssC.handleUpgrade(req, socket, head, (ws) => wssC.emit('connection', ws, req))
        break
    }
  }
  server.on('upgrade', onSwitch)

  const table: Record<string, WebSocketServer> = { '/delta': wssD, '/eps': wssE }
  function onTable(req: http.IncomingMessage, socket: any, head: Buffer) {
    const { pathname } = new URL(req.url || '', 'http://localhost')
    const target = table[pathname]
    if (!target) return
    target.handleUpgrade(req, socket, head, (ws) => target.emit('connection', ws, req))
  }
  server.on('upgrade', onTable)

  const paths = new Map([['/map', wssM]])
  function onMap(req: http.IncomingMessage, socket: any, head: Buffer) {
    const p = new URL(req.url || '', 'http://x').pathname
    const target = paths.get(p)
    target?.handleUpgrade(req, socket, head, (ws) => target?.emit('connection', ws, req))
  }
  server.on('upgrade', onMap)
}
