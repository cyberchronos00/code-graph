export function connect(proto: string) {
  return new WebSocket(`${proto}://${location.host}/events`)
}
