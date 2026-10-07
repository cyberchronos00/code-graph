declare class Pusher {
  constructor(key: string, options?: object)
  subscribe(channel: string): { bind(event: string, callback: (...args: unknown[]) => void): void }
}
export default Pusher
