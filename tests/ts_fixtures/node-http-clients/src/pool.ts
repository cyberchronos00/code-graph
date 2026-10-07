import { request, Client, Pool } from 'undici'
import * as undici from 'undici'

const client = new Client('https://pool.bookbank.example')
const envPool = new Pool(process.env.SETTLEMENT_URL)

export async function settle(batch: string) {
  await request('https://settle.bookbank.example/v1/settlements', { method: 'POST', body: JSON.stringify({ batch, currency: 'EUR' }) })
  await client.request({ path: '/v1/batches', method: 'GET' })
  await envPool.request({ path: '/v1/batches/close', method: 'POST' })
  return undici.request('https://settle.bookbank.example/v1/settlements')
}
