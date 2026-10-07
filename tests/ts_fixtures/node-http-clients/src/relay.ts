import superagent from 'superagent'
import got from 'got'
import https from 'https'

export function viaSuperagent(url: string) {
  return superagent.get(url)
}

export function viaGot(url: string) {
  return got(url)
}

export function viaHttps(opts: any) {
  return https.request(opts, () => {})
}
