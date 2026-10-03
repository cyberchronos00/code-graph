import { contextBridge, ipcRenderer } from 'electron'

contextBridge.exposeInMainWorld('api', {
  readSettings: () => ipcRenderer.invoke('settings:read'),
  saveSettings: (v: string) => ipcRenderer.send('settings:save', v),
  missing: () => ipcRenderer.invoke('settings:missing'),
})

ipcRenderer.on('settings:saved', (_e, ok: boolean) => {
  console.log('saved', ok)
})
