export function formatPrice(cents: number): string {
  return `$${(cents / 100).toFixed(2)}`
}

export function slugify(s: string): string {
  return s.toLowerCase().replace(/\s+/g, '-')
}
