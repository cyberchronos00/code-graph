import express from 'express'

const app = express()

function shippingHook(_req, res) {
  res.end('ok')
}

app.post('/api/webhooks/shipping', shippingHook)
