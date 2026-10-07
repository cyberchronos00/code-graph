export interface AxiosInstance {
  request(config: unknown): Promise<unknown>
  get(url: string, config?: unknown): Promise<unknown>
  delete(url: string, config?: unknown): Promise<unknown>
  head(url: string, config?: unknown): Promise<unknown>
  options(url: string, config?: unknown): Promise<unknown>
  post(url: string, data?: unknown, config?: unknown): Promise<unknown>
  put(url: string, data?: unknown, config?: unknown): Promise<unknown>
  patch(url: string, data?: unknown, config?: unknown): Promise<unknown>
}
export interface AxiosStatic extends AxiosInstance {
  create(config?: { baseURL?: string }): AxiosInstance
}

declare const axios: AxiosStatic
export default axios
