import axios, { type AxiosInstance } from 'axios'

/** Base URL of the backend API for the selected store. */
export const useApiBaseUrl = () => {
  const config = useRuntimeConfig()
  let serverUrl = String(config.public.SERVER_API_URL || 'http://localhost:8000/api')
  const storeSlug = 'main'
  return `${serverUrl}/v1/${storeSlug}`
}

export const useApi = () => {
  const baseURL = useApiBaseUrl()
  const api: AxiosInstance = axios.create({ baseURL })
  return { api }
}
