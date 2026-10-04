const cache = new Map<string, string>()

export function thumbnail(url: string, size: number): string {
  const rendered = render(url, size)
  cache.set(url, rendered)
  return rendered
}

export function badge(url: string, size: number): string {
  const key = `${url}-${size}`
  const rendered = render(url, size)
  cache.set(key, rendered)
  return rendered
}

export function remember(url: string, image: string) {
  cache.set(url, image)
}

function render(url: string, size: number): string {
  return url + size
}
