declare class Echo<T extends string = string> {
  constructor(options?: object)
  private(channel: string): { listen(event: string, callback: (...args: unknown[]) => void): unknown }
  channel(channel: string): unknown
  join(channel: string): unknown
  leave(channel: string): void
  disconnect(): void
}
export default Echo
