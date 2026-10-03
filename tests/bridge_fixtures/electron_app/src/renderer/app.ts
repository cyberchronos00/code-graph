declare global {
  interface Window { api: any }
}

export async function loadSettings() {
  return await window.api.readSettings()
}

export function onSave(v: string) {
  window.api.saveSettings(v)
}

// an object member named like an Object.prototype key is not an IPC object
export function notIpc(x: any) {
  x.constructor.on('settings:read', () => 1)
  x.hasOwnProperty.handle('settings:read', () => 1)
}
