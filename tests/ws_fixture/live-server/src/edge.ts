import Fastify from 'fastify'
import { Hono } from 'hono'
import { upgradeWebSocket } from 'hono/cloudflare-workers'
import { streamSSE } from 'hono/streaming'

const f = Fastify()
f.get('/ws/chat', { websocket: true }, (socket, req) => {
  socket.on('message', () => socket.send('ok'))
})
f.get('/health', async () => ({ ok: true }))
f.listen({ port: 3001 })

const h = new Hono()
h.get('/ws/hono', upgradeWebSocket((c) => ({ onMessage(evt, ws) { ws.send('x') } })))
h.get('/sse/hono', (c) => streamSSE(c, async (stream) => { await stream.writeSSE({ event: 'tick', data: '1' }) }))
export default h
