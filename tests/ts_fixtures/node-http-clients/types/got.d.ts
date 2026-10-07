declare module 'got' {
  interface Got { (url: string, opts?: any): any; get(url: string, opts?: any): any; post(url: string, opts?: any): any; put(url: string, opts?: any): any; extend(opts: any): Got }
  const got: Got
  export default got
}
