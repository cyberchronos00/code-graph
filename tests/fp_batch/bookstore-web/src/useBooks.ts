import axios from 'axios'

const api = axios.create({ baseURL: '/api' })

export const list = (page: number) =>
  api.get('/books', { params: { genre: 'poetry', page, per_page: 50, in_stock_only: true, search: 'moon' } })

export const listTracked = (page: number) =>
  api.get('/books', { params: { genre: 'poetry', page, utm_source: 'mail' } })

export const browse = () =>
  api.get('/books/browse', { params: { genre: 'poetry', author: 'a', lang: 'en', edition: 2, internal: 1, year: 1999, rating: 4,
    published: '2020-01-01', shelf: 's', series: 'x', sort: 'title', page: 2, cursor: 'abc', per_page: 5, utm_source: 'mail' } })

export const shelves = () => api.get('/shelves', { params: { room: 'r', per_page: 5, page: 2, utm_source: 'mail' } })

export const viaRequest = () => api.request({ method: 'GET', url: '/books', params: { genre: 'poetry', page: 1 } })
