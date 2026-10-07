import crypto from 'crypto'

export function looseHook(req, res) {
  const sig = req.header(PARTNER_SIGNATURE)
  const expect = crypto.createHmac('sha256', 'secret').update(req.body).digest('hex')
  if (!crypto.timingSafeEqual(Buffer.from(String(sig)), Buffer.from(expect))) return res.status(401).end()
  res.end('ok')
}

export function registerLoose(router) {
  router.post('/hooks/loose', looseHook)
}
