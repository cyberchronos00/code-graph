import { Pool } from 'pg'

export const pool = new Pool({ host: 'pg.bookstore.example', user: 'shop', password: 'SURF-TS-PG-PW-2b8d', database: 'shop' })

export async function saveOrder(title: string) {
  return pool.query('INSERT INTO orders (title) VALUES ($1)', [title])
}

export async function purgeOrders() {
  return pool.query('DELETE FROM orders')
}
