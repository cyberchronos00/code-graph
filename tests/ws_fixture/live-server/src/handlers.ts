export function onLive(ws, req) {
  ws.on('message', (data) => ws.send(data))
}

export function onEcho(ws, req) {
  ws.on('message', (m) => ws.send(m))
}

export function streamEvents(req, res) {
  res.setHeader('Content-Type', 'text/event-stream')
  res.write('event: tick\ndata: 1\n\n')
}

export async function plain(req, res) {
  // asks an upstream for a stream, but answers JSON itself: not an SSE route
  const r = await fetch('https://upstream.example.com/feed', { headers: { Accept: 'text/event-stream' } })
  res.json({ ok: r.ok })
}
