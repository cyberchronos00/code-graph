let current = ''

export function readSettings(): string {
  return current
}

export function saveSettings(value: string) {
  current = value
}
