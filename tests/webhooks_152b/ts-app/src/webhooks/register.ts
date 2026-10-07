export async function createShipment(order: { id: string }) {
  const url = `${process.env.APP_URL}/api/webhooks/shipping`
  await fetch('https://carrier.test/v1/shipments', {
    method: 'POST',
    body: JSON.stringify({ order: order.id, notify_url: url }),
  })
}

export async function createPartnerShipment(order: { id: string }) {
  await fetch('https://carrier.test/v1/partner', {
    method: 'POST',
    body: JSON.stringify({ order: order.id, notify_url: `${process.env.PARTNER_URL}/api/webhooks/shipping` }),
  })
}

export async function registerWithSdk(client: any) {
  await client.webhooks.create({ url: `${process.env.APP_URL}/api/webhooks/shipping`, events: ['shipment.updated'] })
}
