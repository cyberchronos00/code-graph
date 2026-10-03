import { Pool } from 'pg'
import Redis from 'ioredis'

export function pool() {
  return new Pool({ connectionString: process.env.DATABASE_URL })
}

export function cache() {
  return new Redis(process.env.REDIS_URL)
}

export async function syncOrders() {
  const rows = await pool().query('select * from orders')
  await cache().set('orders', JSON.stringify(rows))
}
