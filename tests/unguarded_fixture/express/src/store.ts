import { Pool } from 'pg'

const pool = new Pool({ host: 'localhost' })

export async function saveOrder(title: string) {
  return pool.query('INSERT INTO orders (title) VALUES ($1)', [title])
}
