chrome.runtime.onConnectExternal.addListener((port: any) => {
  if (port.sender.origin !== 'https://shop.bookstore.example') {
    port.disconnect()
    return
  }
  port.postMessage({ type: 'hello' })
})

chrome.runtime.onMessageExternal.addListener((msg: any, sender: any, reply: any) => {
  if (sender.id !== 'abcdefghijklmnopabcdefghijklmnop') {
    return
  }
  reply({ items: [] })
})
