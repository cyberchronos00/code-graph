import ReconnectingWebSocket from 'reconnecting-websocket'
import { fetchEventSource } from '@microsoft/fetch-event-source'

const API = import.meta.env.VITE_API_URL
const proto = location.protocol === 'https:' ? 'wss' : 'ws'

export function connectLive() {
  const ws = new WebSocket(`${proto}://${location.host}/live`)
  ws.onmessage = (e) => console.log(e.data)
  return ws
}

export function connectEcho() {
  return new ReconnectingWebSocket(`${location.origin.replace(/^http/, 'ws')}/api/echo`)
}

export function connectMetrics() {
  return new WebSocket('ws://localhost:9100')
}

export function events() {
  const es = new EventSource(`${API}/events`)
  es.addEventListener('tick', (e) => console.log(e))
  return es
}

export function chat() {
  return new WebSocket('/ws/chat')
}

export async function hono() {
  await fetchEventSource('/sse/hono', { method: 'GET', onmessage(m) { console.log(m.data) } })
}

export async function collab(doc: string) {
  return new WebSocket(`${API.replace(/^http/, 'ws')}/collab/${doc}`)
}

export function terminal(tokenData: { url: string, token: string }) {
  // the whole URL comes from the server: no endpoint
  return new WebSocket(`${tokenData.url}?token=${tokenData.token}`)
}
