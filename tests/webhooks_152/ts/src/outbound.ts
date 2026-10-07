import crypto from 'crypto'

export const SALEOR_SIGNATURE_HEADER = 'Saleor-Signature'

export async function notifyOrderPaid(url: string, body: object) {
  const raw = JSON.stringify({ event: 'order.paid', body })
  const sig = crypto.createHmac('sha256', 'secret').update(raw).digest('hex')
  await fetch(url, {
    method: 'POST',
    body: raw,
    headers: { [SALEOR_SIGNATURE_HEADER]: sig, 'content-type': 'application/json' },
  })
}
