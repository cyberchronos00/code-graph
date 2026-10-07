import axios from 'axios'

declare const runtimeConfig: { paymentsBase: string }
const api = axios.create({ baseURL: runtimeConfig.paymentsBase })

export const requestRefund = (orderId: string) => api.post('/refunds', { order_id: orderId, reason: 'damaged' })
