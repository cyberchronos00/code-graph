export function announceCart(frame: HTMLIFrameElement) {
  frame.contentWindow.postMessage({ type: 'cart', items: 3 }, '*')
}

export function announceCartPinned(frame: HTMLIFrameElement) {
  frame.contentWindow.postMessage({ type: 'cart', items: 3 }, 'https://shop.bookstore.example')
}

window.addEventListener('message', (event: MessageEvent) => {
  if (event.origin !== 'https://shop.bookstore.example') {
    return
  }
  console.log(event.data.type)
})

window.addEventListener('message', (event: MessageEvent) => {
  if (event.data.type === 'theme') {
    document.body.className = event.data.value
  }
})
