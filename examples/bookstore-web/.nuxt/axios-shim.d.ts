// minimal stand-in for axios types (the sample has no node_modules)
export interface AxiosRequestConfig { baseURL?: string; params?: unknown }
export interface AxiosInstance {
  get<T = any>(url: string, config?: AxiosRequestConfig): Promise<{ data: T }>
  post<T = any>(url: string, data?: unknown, config?: AxiosRequestConfig): Promise<{ data: T }>
  delete<T = any>(url: string, config?: AxiosRequestConfig): Promise<{ data: T }>
}
export interface AxiosStatic extends AxiosInstance { create(config: AxiosRequestConfig): AxiosInstance }
declare const axios: AxiosStatic
export default axios
