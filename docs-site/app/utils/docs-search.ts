/**
 * Nuxt Content FTS uses the unicode61 tokenizer: "_" and "-" are separators,
 * and terms are AND-ed. "MATCHES_ROUTE" is stored as MATCHES + ROUTE.
 */
export function toDocsSearchQuery(raw: string): string {
  const terms = raw
    .split(/[\s_-]+/)
    .map(term => term.trim())
    .filter(term => term.length > 0)
  return [...new Set(terms)].join(' ')
}
