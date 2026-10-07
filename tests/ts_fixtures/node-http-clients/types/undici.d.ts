declare module 'undici' {
  export function request(url: any, opts?: any): Promise<any>
  export function fetch(url: any, init?: any): Promise<any>
  export class Client { constructor(origin: any, opts?: any); request(opts: any): Promise<any> }
  export class Pool { constructor(origin: any, opts?: any); request(opts: any): Promise<any> }
}
