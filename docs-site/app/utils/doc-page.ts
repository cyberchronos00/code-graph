/** Drop the markdown H1 and intro when the doc chrome already shows them. */

type TocLink = { depth?: number, text?: string, title?: string }

export type DocPageChrome = {
  title?: string | null
  description?: string | null
  body?: {
    value?: unknown[]
    toc?: { links?: TocLink[] }
  } | null
}

function norm(value: string): string {
  return value.replace(/\s+/g, ' ').trim()
}

function nodeTag(node: unknown): string | null {
  if (Array.isArray(node) && typeof node[0] === 'string') {
    return node[0]
  }
  if (node && typeof node === 'object' && 'tag' in node && typeof node.tag === 'string') {
    return node.tag
  }
  return null
}

function nodeText(node: unknown): string {
  if (typeof node === 'string' || typeof node === 'number') {
    return String(node)
  }
  if (Array.isArray(node)) {
    const start = typeof node[0] === 'string' ? 2 : 0
    return node.slice(start).map(nodeText).join('')
  }
  if (node && typeof node === 'object' && 'children' in node && Array.isArray(node.children)) {
    return node.children.map(nodeText).join('')
  }
  return ''
}

export function stripDocChrome(page: DocPageChrome): void {
  const value = page.body?.value
  if (!Array.isArray(value) || value.length === 0) {
    return
  }

  const title = norm(page.title || '')
  if (title && nodeTag(value[0]) === 'h1' && norm(nodeText(value[0])) === title) {
    value.shift()
  }

  const description = norm(page.description || '')
  if (description && nodeTag(value[0]) === 'p') {
    const para = norm(nodeText(value[0]))
    const same = para === description
      || (description.length >= 40 && (para.startsWith(description) || description.startsWith(para)))
    if (same) {
      value.shift()
    }
  }

  const links = page.body?.toc?.links
  if (!Array.isArray(links) || !page.body?.toc) {
    return
  }
  page.body.toc.links = links.filter((link) => {
    if (link.depth !== 1) {
      return true
    }
    const text = norm(link.text || link.title || '')
    return Boolean(title) && text !== title
  })
}
