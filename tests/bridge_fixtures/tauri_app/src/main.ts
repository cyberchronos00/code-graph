import { invoke } from '@tauri-apps/api/core'

export async function greet(name: string): Promise<string> {
  return await invoke<string>('greet', { name })
}

export async function counter() {
  return invoke('increment')
}

export async function typo() {
  return invoke('gret')
}

export async function readText() {
  return invoke('plugin:fs|read_text_file', { path: 'a.txt' })
}

export async function hidden() {
  return invoke('secret')
}

export async function showMenu() {
  return invoke('plugin:app-menu|popup')
}
