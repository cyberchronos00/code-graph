export function profileRoute() {
  return { name: 'settings-profile' }
}
export function bookmarkRoute() {
  return '/bookmarks'
}
export function dynamicRoute(id: string) {
  return other(id)
}
function other(id: string) {
  return '/users/' + id
}
