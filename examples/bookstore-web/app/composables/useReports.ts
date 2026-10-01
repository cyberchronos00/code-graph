import { topParams, type TopQuery } from '~/utils/params'

/**
 * Reports API.
 */
export const useReports = () => {
  const { api } = useApi()
  /** Top sellers report. */
  const fetchTop = async (q: TopQuery) => (await api.get('/admin/reports/top', { params: topParams(q) })).data
  const exportTop = async (format: 'csv' | 'xlsx') => {
    const endpoint = `/admin/reports/top/export.${format}`
    return api.get(endpoint)
  }
  const removeReport = (id: number) => api.delete(`/admin/reports/${id}`)
  return { fetchTop, exportTop, removeReport }
}

/** Same-origin static file via $fetch (not a backend URL). */
export const useAppVersion = () => $fetch('/version.json', { query: { t: Date.now() } })
