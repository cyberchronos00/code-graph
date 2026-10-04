function shrink(blob: Blob) {
  return blob
}

self.onmessage = (event: MessageEvent) => {
  const out = shrink(event.data.blob)
  self.postMessage({ done: true, out })
}
