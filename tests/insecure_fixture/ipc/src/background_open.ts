chrome.runtime.onConnectExternal.addListener((port: any) => {
  port.postMessage({ type: 'hello' })
})

chrome.runtime.onMessageExternal.addListener((msg: any, sender: any, reply: any) => {
  if (msg.type === 'cart') {
    reply({ items: [] })
  }
})
