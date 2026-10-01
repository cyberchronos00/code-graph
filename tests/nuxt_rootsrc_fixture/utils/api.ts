export function useApiClient() {
  const config = useRuntimeConfig()
  return $fetch.create({ baseURL: config.public.apiBase as string })
}
