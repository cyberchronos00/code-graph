const worker = new Worker(new URL('./workers/resize.worker.ts', import.meta.url), { type: 'module' })

export function onResult(event: MessageEvent) {
  console.log(event.data)
}

export function resize(blob: Blob) {
  worker.postMessage({ type: 'resize', blob })
}

worker.onmessage = onResult
