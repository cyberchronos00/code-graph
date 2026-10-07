import { Client } from 'ssh2'

export function openStockLink() {
  const conn = new Client()
  conn.connect({ host: 'stock.bookstore.example', username: 'sync', hostVerifier: () => true })
  return conn
}

export function openStockLinkPinned(expected: string) {
  const conn = new Client()
  conn.connect({ host: 'stock.bookstore.example', username: 'sync', hostVerifier: (key: string) => key === expected })
  return conn
}
