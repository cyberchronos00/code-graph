import crypto from 'crypto'

export const PARTNER_SIGNATURE = 'X-Partner-Signature'

function deploy() {
  return 'deployed'
}

export function githubWebhook(req, res) {
  const event = req.headers['x-github-event']
  if (event === 'push') deploy()
  res.end('ok')
}

export function registerGithub(router) {
  router.post('github.webhooks', githubWebhook)
}

export function partnerHook(req, res) {
  const sig = req.header(PARTNER_SIGNATURE)
  const expect = crypto.createHmac('sha256', 'secret').update(req.body).digest('hex')
  if (!crypto.timingSafeEqual(Buffer.from(String(sig)), Buffer.from(expect))) return res.status(401).end()
  res.end('ok')
}

export function registerPartner(router) {
  router.post('/hooks/partner', partnerHook)
}
