import express from 'express'
import axios from 'axios'
import dns from 'node:dns'

const app = express()
const ALLOWED_HOSTS = new Set(['covers.bookstore.test', 'isbn.bookstore.test'])

async function proxyCover(req: any, res: any) {
  const target = req.query.url
  const r = await axios.get(target)
  res.json(r)
}

async function coverByIsbn(req: any, res: any) {
  const r = await axios.get(`https://covers.bookstore.test/images/${req.params.isbn}.jpg`)
  res.json(r)
}

async function notifyCallback(req: any, res: any) {
  const u = new URL(req.body.callback)
  if (!ALLOWED_HOSTS.has(u.host)) {
    return res.sendStatus(400)
  }
  await axios.post(u.toString(), { ok: true })
  res.sendStatus(204)
}

async function pullFeed(url: string) {
  return axios.get(url)
}

async function importFeed(req: any, res: any) {
  const feed = await pullFeed(req.body.feed)
  res.json(feed)
}

async function resolveMirror(req: any, res: any) {
  dns.lookup(req.query.host, (err: any, address: string) => res.json({ address }))
}

async function fixedHost(req: any, res: any) {
  const r = await axios.get('https://isbn.bookstore.test/lookup?code=' + req.query.code)
  res.json(r)
}

async function fetchRaw(req: any, res: any) {
  const { link } = req.body
  const r = await fetch(link)
  res.json(await r.json())
}

app.get('/cover', proxyCover)
app.get('/covers/:isbn', coverByIsbn)
app.post('/notify', notifyCallback)
app.post('/import', importFeed)
app.get('/mirror', resolveMirror)
app.get('/lookup', fixedHost)
app.post('/raw', fetchRaw)
export default app
