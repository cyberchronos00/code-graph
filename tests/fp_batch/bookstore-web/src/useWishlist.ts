import axios from 'axios'

const api = axios.create({ baseURL: '/api' })

export const removeItem = (payload: { book_id: number; reason?: string }) =>
  api.delete('/wishlist/items', { data: payload })

export const removeAll = () => api.request({ method: 'DELETE', url: '/wishlist/items', data: { book_id: 0 } })

export const removeViaFetch = (bookId: number) =>
  $fetch('/api/wishlist/items', { method: 'DELETE', body: { book_id: bookId } })

export const removeViaModule = () => axios.delete('/api/wishlist/items', { data: { book_id: 1, reason: 'dup' } })

export const removeWithoutKey = () => api.delete('/wishlist/items', { data: { reason: 'oops' } })

export const removeWithExtra = () => api.delete('/wishlist/items', { data: { book_id: 3, coupon: 'x' } })

export const probe = () => api.head('/wishlist/items', { data: { book_id: 2 } })
