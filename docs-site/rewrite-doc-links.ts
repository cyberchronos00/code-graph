const REPO = 'https://github.com/cyberchronos00/code-graph'

function splitHash(target: string): { path: string, hash: string } {
  const hashAt = target.indexOf('#')
  if (hashAt === -1) {
    return { path: target, hash: '' }
  }
  return { path: target.slice(0, hashAt), hash: target.slice(hashAt) }
}

function rewriteTarget(target: string): string | null {
  if (
    target.startsWith('http://')
    || target.startsWith('https://')
    || target.startsWith('mailto:')
    || target.startsWith('#')
    || target.startsWith('/')
  ) {
    return null
  }

  const { path, hash } = splitHash(target)
  if (path.startsWith('../')) {
    const rel = path.replace(/^(\.\.\/)+/, '')
    return `${REPO}/blob/main/${rel}${hash}`
  }

  const cleaned = path.replace(/^\.\//, '')
  if (cleaned.endsWith('.md')) {
    const slug = cleaned.slice(0, -3)
    return `/docs/${slug}${hash}`
  }

  if (/\.(png|gif|jpe?g|svg|webp|mp4|webm)$/i.test(cleaned)) {
    const file = cleaned.replace(/^(?:docs\/)?media\//, '')
    return `/media/${file}${hash}`
  }

  return null
}

/** Point repo-relative doc links at this site; leave anchors and absolute URLs alone. */
export function rewriteDocLinks(body: string): string {
  return body.replace(/\]\((<)?([^)\s>]+)(>)?\)/g, (full, _open, target: string) => {
    const next = rewriteTarget(target)
    if (!next) {
      return full
    }
    return `](${next})`
  })
}
