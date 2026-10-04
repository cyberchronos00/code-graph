import { MessageType } from './messages'

declare const chrome: any

export function askSettings() {
  chrome.runtime.sendMessage({ type: 'getSettings' })
}

export function highlight(tabId: number) {
  chrome.tabs.sendMessage(tabId, { type: 'highlight' })
}

export function openDevtools() {
  return chrome.runtime.connect({ name: 'devtools' })
}

export function saveAll() {
  chrome.runtime.sendMessage({ type: MessageType.SAVE_ALL })
}
