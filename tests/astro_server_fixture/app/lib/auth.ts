export function currentUser(token: string | undefined): string | null {
  return token ? 'reader' : null
}
