declare const chrome: any

function loadSettings() {
  return {}
}

chrome.runtime.onMessage.addListener((msg, sender, reply) => {
  switch (msg.type) {
    case 'getSettings':
      reply(loadSettings())
      break
    case 'save':
      break
  }
})

chrome.runtime.onConnect.addListener((port) => {
  if (port.name === 'devtools') {
    port.postMessage({ hello: true })
  }
})

export const hostPort = chrome.runtime.connectNative('com.acme.host')
