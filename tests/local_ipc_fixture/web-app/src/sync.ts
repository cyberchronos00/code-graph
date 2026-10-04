const SYNC_CHANNEL = 'app-sync'
const channel = new BroadcastChannel(SYNC_CHANNEL)

export function publishLogout() {
  channel.postMessage({ event: 'logout' })
}
