export function formatDate(ms: number): string {
  return new Date(ms).toISOString().slice(0, 10)
}
