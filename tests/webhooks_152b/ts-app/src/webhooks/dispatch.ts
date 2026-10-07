import crypto from 'crypto'

export async function dispatch(webhook: { event_type: string; url: string; secret: string }, payload: object) {
  const raw = JSON.stringify({ event: webhook.event_type, payload })
  const sig = crypto.createHmac('sha256', webhook.secret).update(raw).digest('hex')
  await fetch(webhook.url, { method: 'POST', body: raw, headers: { 'X-Bookstore-Signature': sig } })
}
