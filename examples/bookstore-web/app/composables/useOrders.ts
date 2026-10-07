import type { AxiosInstance } from 'axios'

/**
 * Orders API. `bookId` is the client spelling of the server's `book_id`.
 */
export const useOrders = () => {
  const { api } = useApi()
  const placeOrder = (bookId: number, qty: number) => api.post('/orders', { bookId, quantity: qty })
  return { placeOrder }
}

export type OrdersApi = AxiosInstance
