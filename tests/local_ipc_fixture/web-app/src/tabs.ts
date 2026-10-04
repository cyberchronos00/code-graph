const bc = new BroadcastChannel('app-sync')

export function onSync(event: MessageEvent) {
  console.log(event.data)
}

bc.addEventListener('message', onSync)
