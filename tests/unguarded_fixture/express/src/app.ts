import express from 'express'
import session from 'express-session'
import { saveOrder } from './store'

const app = express()
app.use(session({ secret: 'fake-bookstore-secret-0000' }))

function requireLogin(req, res, next) {
  if (!req.session.userId) return res.sendStatus(401)
  next()
}

app.get('/healthz', (req, res) => res.send('ok'))
app.post('/login', (req, res) => res.json({ ok: true }))
app.post('/signup', (req, res) => res.json({ ok: true }))
app.get('/catalog/open', (req, res) => res.json([]))

app.use(requireLogin)

app.post('/orders', async (req, res) => {
  await saveOrder(req.body.title)
  res.json({ ok: true })
})

const staff = express.Router()
staff.use(requireLogin)
staff.get('/reports', (req, res) => res.json([]))
app.use('/staff', staff)

app.listen(3000)
