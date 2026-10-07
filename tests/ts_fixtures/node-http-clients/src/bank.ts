import https from 'node:https'

const HOSTS = { stage: 'api.stage.bookbank.example', prod: 'api.bookbank.example' } as const

export function charge(env: keyof typeof HOSTS, body: string) {
  return new Promise((resolve, reject) => {
    const req = https.request({ hostname: HOSTS[env], path: '/v1/charges', method: 'POST' }, resolve)
    req.on('error', reject)
    req.end(body)
  })
}
