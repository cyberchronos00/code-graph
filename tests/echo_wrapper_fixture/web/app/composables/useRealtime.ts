import Echo from 'laravel-echo'

const echo = new Echo({ broadcaster: 'reverb' })

export function privateChannel(name: string) {
  return echo.private(name)
}

export function presenceChannel(name: string) {
  return echo.join(name)
}

export function ordersChannel(id: number | string) {
  return privateChannel(`store.${id}.orders`)
}

export function watchHeld() {
  echo.private('store.1.orders').listen('.OrderHeld', () => {})
}
