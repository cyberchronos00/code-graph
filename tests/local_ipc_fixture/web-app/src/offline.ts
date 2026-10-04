export function onSwMessage(event: MessageEvent) {
  console.log('sw says', event.data.type)
}

export function cacheNow() {
  navigator.serviceWorker.controller?.postMessage({ type: 'CACHE' })
}

navigator.serviceWorker.addEventListener('message', onSwMessage)
