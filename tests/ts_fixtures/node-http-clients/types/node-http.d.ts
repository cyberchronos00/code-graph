declare module 'node:http' { const http: any; export default http; export const request: any; export const get: any; export function createServer(...a: any[]): any }
declare module 'node:https' { const https: any; export default https; export const request: any; export const get: any }
declare module 'http' { const http: any; export default http; export const request: any; export const get: any; export function createServer(...a: any[]): any }
declare module 'https' { const https: any; export default https; export const request: any; export const get: any }
declare var process: { env: Record<string, string | undefined> }
