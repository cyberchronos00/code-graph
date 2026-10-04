import express from 'express'
import crypto from 'crypto'
import Stripe from 'stripe'
import { Webhook } from 'svix'
import { Webhooks } from '@octokit/webhooks'
import { activateSubscription, markInvoicePaid, audit } from './billing'

const app = express()
const stripe = new Stripe(process.env.STRIPE_KEY)
const webhooks = new Webhooks({ secret: process.env.GH_SECRET })

app.post('/webhooks/stripe', express.raw({ type: 'application/json' }), async (req, res) => {
  const sig = req.headers['stripe-signature']
  const event = stripe.webhooks.constructEvent(req.body, sig, process.env.STRIPE_WH)
  switch (event.type) {
    case 'checkout.session.completed':
      await activateSubscription(event.data.object)
      audit('checkout')
      break
    case 'invoice.paid':
      await markInvoicePaid(event.data.object)
      audit('invoice')
      break
    default:
      audit('other')
  }
  res.sendStatus(200)
})

// reads the signature header but never checks it
app.post('/hooks/stripe-legacy', express.json(), async (req, res) => {
  const sig = req.headers['stripe-signature']
  const event = req.body
  if (event.type === 'customer.subscription.deleted') {
    audit(String(sig))
  }
  res.sendStatus(200)
})

app.post('/webhooks/svix', express.raw({ type: 'application/json' }), (req, res) => {
  const wh = new Webhook(process.env.SVIX_SECRET)
  const msg: any = wh.verify(req.body, req.headers as any)
  if (msg.type === 'user.created') {
    audit('user')
  }
  res.sendStatus(204)
})

app.post('/webhooks/github', express.raw({ type: 'application/json' }), (req, res) => {
  const name = req.headers['x-github-event']
  const expected = 'sha256=' + crypto.createHmac('sha256', process.env.GH_SECRET).update(req.body).digest('hex')
  const got = String(req.headers['x-hub-signature-256'])
  if (!crypto.timingSafeEqual(Buffer.from(expected), Buffer.from(got))) {
    return res.sendStatus(401)
  }
  if (name === 'push') {
    audit('push')
  }
  res.sendStatus(200)
})

// GitHub events without a signature check
app.post('/hooks/github-open', express.json(), (req, res) => {
  if (req.headers['x-github-event'] === 'issues') {
    audit('issues')
  }
  res.sendStatus(200)
})

app.post('/hooks/partner', express.raw({ type: 'application/json' }), (req, res) => {
  const mac = crypto.createHmac('sha256', process.env.PARTNER_SECRET).update(req.body).digest('hex')
  if (!crypto.timingSafeEqual(Buffer.from(mac), Buffer.from(String(req.headers['x-partner-signature'])))) {
    return res.sendStatus(401)
  }
  res.sendStatus(200)
})

app.post('/api/orders', express.json(), (req, res) => {
  res.json({ ok: true })
})

webhooks.on('pull_request', onPullRequest)

export async function onPullRequest({ payload }) {
  return payload.number
}

app.listen(3000)
