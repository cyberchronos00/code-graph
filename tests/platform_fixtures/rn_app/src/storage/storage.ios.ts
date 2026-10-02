export function save(key: string): string {
  return keychainWrite(key)
}

function keychainWrite(key: string): string {
  return `keychain:${key}`
}
