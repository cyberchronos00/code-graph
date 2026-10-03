import { app } from 'electron'
import { IpcEvents } from '../ipc-events'
import { ipcMainManager } from './ipc-manager'

export function setupLifecycle() {
  ipcMainManager.on(IpcEvents.QUIT, app.quit)
  ipcMainManager.on(IpcEvents.THEME, (event: any) => {
    event.sender.send(IpcEvents.THEME, 'dark')
  })
}

export function runFiddle() {
  ipcMainManager.send(IpcEvents.RUN)
}
