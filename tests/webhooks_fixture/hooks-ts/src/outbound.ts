import crypto from 'crypto'
import { Svix } from 'svix'

const svix = new Svix(process.env.SVIX_TOKEN)

export async function announceInvoice(appId: string, invoice) {
  await svix.message.create(appId, { eventType: 'invoice.paid', payload: invoice })
}

export async function deliver(event: string, url: string, body: object) {
  const raw = JSON.stringify({ event, body })
  const sig = crypto.createHmac('sha256', process.env.OUT_SECRET).update(raw).digest('hex')
  return fetch(url, { method: 'POST', body: raw, headers: { 'X-Acme-Signature': sig } })
}

export async function orderCreated(order) {
  await deliver('order.created', order.hookUrl, order)
}
