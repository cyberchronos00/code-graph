import axios from 'axios'
import https from 'https'

export async function testPartnerFeed() {
  const agent = new https.Agent({ rejectUnauthorized: false })
  return axios.get('https://feed.bookstore.example/books', { httpsAgent: agent })
}
