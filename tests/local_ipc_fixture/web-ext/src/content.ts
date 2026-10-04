declare const chrome: any

export function onMessage(message: any) {
  if (message.type === 'highlight') {
    document.body.classList.add('hl')
  }
}

chrome.runtime.onMessage.addListener(onMessage)
