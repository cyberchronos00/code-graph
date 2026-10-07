declare module 'https' { export class Agent { constructor(opts?: unknown) } }
declare module 'ssh2' { export class Client { connect(opts: unknown): void } }
declare module '@grpc/grpc-js' {
  export const credentials: { createInsecure(): unknown; createSsl(): unknown }
  export const ServerCredentials: { createInsecure(): unknown; createSsl(): unknown }
  export class Server { bindAsync(addr: string, creds: unknown, cb: () => void): void }
}
