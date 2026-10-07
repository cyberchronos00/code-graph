import messagebird from 'messagebird'
import plivo from 'plivo'

export async function viaMessagebird(to: string) {
  const mb = messagebird.initClient(process.env.MESSAGEBIRD_API_KEY as string)
  mb.messages.create({ originator: 'Bookstore', recipients: [to], body: 'Shipped' }, () => {})
}

export async function viaPlivo(to: string) {
  const client = new plivo.Client(process.env.PLIVO_AUTH_ID, process.env.PLIVO_AUTH_TOKEN)
  await client.messages.create('+15550001', to, 'Shipped')
}
