export function onAnyMessage(event: MessageEvent) {
  document.title = String(event.data)
}

window.addEventListener('message', onAnyMessage)
