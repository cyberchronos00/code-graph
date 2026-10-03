import express from 'express'
import { syncOrders } from './db'

const app = express()

app.post('/sync', async (_req, res) => {
  await syncOrders()
  res.json({ ok: true })
})

app.listen(3000)
