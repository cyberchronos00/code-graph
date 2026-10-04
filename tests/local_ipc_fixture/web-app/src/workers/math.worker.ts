import { expose } from 'comlink'

export const api = {
  add(a: number, b: number) {
    return a + b
  },
}

expose(api)
