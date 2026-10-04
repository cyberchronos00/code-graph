let worker: Worker | null = null

function getWorker() {
  if (!worker) {
    worker = new Worker(new URL('./workers/resize.worker.ts', import.meta.url))
  }
  return worker
}

export function shrinkLater(blob: Blob) {
  getWorker().postMessage({ blob })
}

export class Channel {
  #onmessage: ((m: unknown) => void) | null = null

  listen(handler: (m: unknown) => void) {
    this.#onmessage = handler
  }
}
