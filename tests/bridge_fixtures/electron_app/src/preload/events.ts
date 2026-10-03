import { contextBridge, ipcRenderer } from 'electron'
import { IpcEvents } from '../ipc-events'

const table = { 'run-fiddle': IpcEvents.RUN, 'theme-changed': IpcEvents.THEME } as const

export function addEventListener(type: keyof typeof table, listener: (...a: any[]) => void) {
  const channel = table[type]
  ipcRenderer.on(channel, (_e, ...args) => listener(...args))
}

contextBridge.exposeInMainWorld('fiddle', {
  addEventListener,
  quit: () => ipcRenderer.send(IpcEvents.QUIT),
})
