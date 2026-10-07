import axios from 'axios'

interface CreateOrderInput {
  bookId: number
  quantity: number
  giftWrap?: boolean
}

export function placeFromVar(bookId: number, qty: number) {
  const payload = { bookId, quantity: qty }
  return axios.post('/orders', payload)
}

export function placeTyped(payload: CreateOrderInput) {
  return axios.post('/orders/typed', payload)
}

export function placeNested() {
  return axios.post('/orders/nested', { address: { city: 'Lisbon' }, items: [{ book_id: 1 }] })
}

export function placeItems(rows: unknown[]) {
  return axios.post('/orders/items', { items: rows })
}

export function placeSpread(extra: Record<string, unknown>) {
  return axios.post('/orders/spread', { ...extra, quantity: 1 })
}

export function placeAssign(extra: Record<string, unknown>) {
  return axios.post('/orders/assign', Object.assign({ quantity: 1 }, extra))
}

export function placeForm() {
  const body = new FormData()
  body.append('book_id', '1')
  return axios.post('/orders/form', body)
}

function buildPayload() {
  const payload = { bookId: 1, extra: true }
  return payload
}

export function placeAcross() {
  const payload = buildPayload()
  return axios.post('/orders/across', payload)
}
