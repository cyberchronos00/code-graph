import express from 'express'
import { notifyShipped } from './push'

const app = express()

app.post('/orders/:id/ship', async (req, res) => {
  await notifyShipped(req.body.token)
  res.json({ ok: true })
})

export default app
