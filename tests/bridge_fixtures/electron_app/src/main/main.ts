import { app, BrowserWindow, ipcMain } from 'electron'
import { readSettings, saveSettings } from './settings'

let win: BrowserWindow | null = null

export function createWindow() {
  win = new BrowserWindow({ webPreferences: { preload: 'preload.js' } })
  win.loadFile('index.html')
}

ipcMain.handle('settings:read', async () => readSettings())

ipcMain.on('settings:save', (_event, value: string) => {
  saveSettings(value)
  notifySaved()
})

ipcMain.handle('app:unused', () => 1)

export function notifySaved() {
  win?.webContents.send('settings:saved', true)
}

app.whenReady().then(createWindow)
