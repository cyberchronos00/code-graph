declare const self: any

self.addEventListener('install', () => self.skipWaiting())

async function handleSwMessage(event: any) {
  if (event.data.type === 'CACHE') {
    const all = await self.clients.matchAll()
    for (const client of all) {
      client.postMessage({ type: 'CACHED' })
    }
  }
}

self.addEventListener('message', handleSwMessage)
