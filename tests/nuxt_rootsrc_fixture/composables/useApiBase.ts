// the API origin: a remote override when one is set, else the configured default
export function useApiBase() {
  const config = useRuntimeConfig()
  const remote = useState<string | null>('api-override', () => null)
  return computed(() => normaliseBase(remote.value, config.public.apiBase as string))
}

function normaliseBase(override: string | null, fallback: string): string {
  const value = (override || '').trim()
  if (!value) {
    return String(fallback).replace(/\/+$/, '')
  }
  return value
}
