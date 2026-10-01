import axios from 'axios'

export function useTasks() {
  const config = useRuntimeConfig()
  const apiUrl = config.public.apiBase as string
  const base = useApiBase()
  const http = axios.create({ baseURL: base.value })

  const list = (boardId: number) => $fetch(`${apiUrl}/boards/${boardId}/tasks`)
  const move = (taskId: number, state: string) => http.patch(`/tasks/${taskId}/move`, { state })
  const comment = (taskId: number, body: string) => useApiClient()(`/tasks/${taskId}/comments`, { method: 'POST', body: { body } })
  const status = () => $fetch(`${import.meta.env.VITE_STATUS_URL}/summary.json`)
  return { list, move, comment, status }
}
