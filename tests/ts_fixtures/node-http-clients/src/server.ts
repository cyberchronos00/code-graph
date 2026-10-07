import http from 'node:http'

export function start() {
  return http.createServer((req, res) => { res.end('ok') }).listen(3000)
}
