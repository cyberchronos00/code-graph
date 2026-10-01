export function useSession() {
  // `$session` is auto-imported from utils/ (no import statement)
  const archive = (taskId: number) => $session(`/tasks/${taskId}/archive`, { method: 'post' })
  return { archive }
}

// a thin wrapper: the URL comes from its callers
export function useSessionFetch(url: string) {
  return useFetch(url, { $fetch: $session })
}
