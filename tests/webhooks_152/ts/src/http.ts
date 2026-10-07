import express from 'express'

function order(_req, res) {
  res.end('ok')
}

const app = express()
app.post('/orders', order)
app.post('/acme.events', order)
