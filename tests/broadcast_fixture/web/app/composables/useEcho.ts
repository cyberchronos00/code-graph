import Echo from 'laravel-echo'
import Pusher from 'pusher-js'

let echo: Echo<'reverb'> | null = null

export function useEchoClient(): Echo<'reverb'> {
  if (!echo) {
    echo = new Echo({ broadcaster: 'reverb', key: 'local', authEndpoint: '/api/broadcasting/auth' })
  }
  return echo
}

export function usePusherStatus(key: string) {
  const pusher = new Pusher(key, { cluster: 'eu' })
  const channel = pusher.subscribe('status')
  channel.bind('App\\Events\\StatusPage', () => {})
  return pusher
}

// a channel kept on an object and listened to elsewhere in the file
export const lobby = {
  channel: null as any,
  join(teamId: number) {
    this.channel = useEchoClient().join(`team.${teamId}.lobby`)
    this.bindListeners()
  },
  bindListeners() {
    this.channel.listen('TeamOnline', () => {})
  },
}
