import express from 'express'
import crypto from 'crypto'
import net from 'net'
import { saveOrder, purgeOrders } from './db'
import { sendReceipt, pingPartner } from './notify'

const app = express()

function requireAuth(req, res, next) {
  if (!req.headers.authorization) {
    return res.sendStatus(401)
  }
  next()
}

app.post('/orders', requireAuth, express.json(), async (req, res) => {
  await saveOrder(req.body.title)
  await sendReceipt(req.body.email)
  res.json({ ok: true })
})

app.delete('/admin/orders', async (req, res) => {
  await purgeOrders()
  res.json({ purged: true })
})

app.get('/stock', async (req, res) => {
  res.json(await pingPartner())
})

app.post('/hooks/github-open', express.json(), (req, res) => {
  if (req.headers['x-github-event'] === 'issues') {
    res.sendStatus(202)
    return
  }
  res.sendStatus(200)
})

app.post('/hooks/github', express.raw({ type: 'application/json' }), (req, res) => {
  const expected = 'sha256=' + crypto.createHmac('sha256', process.env.GH_SECRET).update(req.body).digest('hex')
  const got = String(req.headers['x-hub-signature-256'])
  if (!crypto.timingSafeEqual(Buffer.from(expected), Buffer.from(got))) {
    return res.sendStatus(401)
  }
  res.sendStatus(200)
})

app.listen(3000)

function onIngest(socket) {
  socket.on('data', (d) => socket.write(d))
}

function onAdmin(socket) {
  socket.end('bye')
}

export function startIngest() {
  net.createServer(onIngest).listen(7200, '0.0.0.0')
  net.createServer(onAdmin).listen(7201, '127.0.0.1')
}
