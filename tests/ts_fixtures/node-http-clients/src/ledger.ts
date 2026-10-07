import https from 'node:https'
import http from 'http'

const LEDGER_HOST = 'ledger.bookbank.example'

export function listEntries() {
  return https.request({ hostname: LEDGER_HOST, path: '/v2/entries', method: 'GET' }, () => {})
}

export function postEntry(body: string) {
  const req = https.request({ protocol: 'https:', host: 'ledger.bookbank.example', port: 8443, path: '/v2/entries', method: 'POST' }, () => {})
  req.end(body)
}

export function health() {
  return http.get({ hostname: 'status.bookbank.example', path: '/health' }, () => {})
}

export function byUrlString(id: string) {
  return https.get('https://rates.bookbank.example/v1/rates', () => {})
}

export function byUrlObject(id: string) {
  const u = new URL(`/v1/accounts/${id}`, 'https://accounts.bookbank.example')
  return https.request(u, { method: 'DELETE' }, () => {})
}

export function fromEnv() {
  return https.request({ hostname: process.env.BANK_HOST, path: '/v1/balance' }, () => {})
}
