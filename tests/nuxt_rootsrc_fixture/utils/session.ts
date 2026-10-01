import { $fetch } from 'ofetch'

// one shared client for cookie-authenticated calls
export const $session = $fetch.create({ baseURL: '/api', credentials: 'include' })
