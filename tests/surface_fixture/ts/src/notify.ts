import { ServerClient } from 'postmark'

export async function sendReceipt(to: string) {
  const client = new ServerClient('SURF-TS-POSTMARK-55e1')
  await client.sendEmail({ From: 'shop@bookstore.example', To: to, Subject: 'Receipt', TextBody: 'Thanks' })
}

export async function pingPartner() {
  return fetch('http://partner.bookstore.example/v1/stock')
}

export async function pingLocal() {
  return fetch('http://127.0.0.1:3000/health')
}
