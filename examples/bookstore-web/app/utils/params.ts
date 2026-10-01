export type TopQuery = { category_id?: number, timezone?: string, date_from?: string }

export const topParams = (q: TopQuery): Record<string, string | number> => {
  const params: Record<string, string | number> = { mode: 'titles' }
  if (q.category_id != null) params.category_id = q.category_id
  if (q.timezone) params.timezone = q.timezone
  return params
}
