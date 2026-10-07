import { BrowserWindow } from 'electron'

export function openRiskyWindow() {
  return new BrowserWindow({
    width: 800,
    webPreferences: {
      nodeIntegration: true,
      contextIsolation: false,
      webSecurity: false,
    },
  })
}

export function openSafeWindow() {
  return new BrowserWindow({
    width: 800,
    webPreferences: {
      nodeIntegration: false,
      contextIsolation: true,
      webSecurity: true,
    },
  })
}
