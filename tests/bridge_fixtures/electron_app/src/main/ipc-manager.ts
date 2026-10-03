import { ipcMain } from 'electron'
import { IpcEvents } from '../ipc-events'

const relayed: IpcEvents[] = [IpcEvents.QUIT, IpcEvents.THEME, IpcEvents.RUN]

class IpcMainManager {
  constructor() {
    // relay every channel to the manager's listeners: a union-typed channel
    relayed.forEach((name) => {
      ipcMain.on(name, (event, ...args) => this.emit(name, event, ...args))
    })
  }
  emit(_name: string, ..._args: any[]) {}
  on(_name: IpcEvents, _fn: (...args: any[]) => void) {}
  send(_name: IpcEvents, ..._args: any[]) {}
}

export const ipcMainManager = new IpcMainManager()
