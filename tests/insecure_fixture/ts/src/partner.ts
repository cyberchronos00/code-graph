import axios from 'axios'
import https from 'https'

export async function pullPartnerFeed() {
  const agent = new https.Agent({ rejectUnauthorized: false })
  return axios.get('https://feed.bookstore.example/books', { httpsAgent: agent })
}

export async function pullPartnerFeedChecked() {
  const agent = new https.Agent({ rejectUnauthorized: true })
  return axios.get('https://feed.bookstore.example/books', { httpsAgent: agent })
}

export function allowSelfSigned() {
  process.env.NODE_TLS_REJECT_UNAUTHORIZED = '0'
}
