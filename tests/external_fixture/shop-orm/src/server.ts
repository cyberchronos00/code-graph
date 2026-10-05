import * as schema from './schema'
import express from 'express'
import { AppDataSource } from './data-source'
import { Order } from './entities/order.entity'
import { Kysely, PostgresDialect } from 'kysely'
import { Pool } from 'pg'

const db = new Kysely({ dialect: new PostgresDialect({ pool: new Pool({ connectionString: process.env.DATABASE_URL }) }) })
const app = express()

export async function listOrders() {
  return AppDataSource.getRepository(Order).find()
}

export async function listAssets() {
  return db.selectFrom('asset').selectAll().execute()
}

app.get('/orders', async (_req, res) => { res.json(await listOrders()) })
app.listen(3000)

export const tables = schema
