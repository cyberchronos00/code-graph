import { Elysia } from 'elysia'

export function makeShelf(prefix: string) {
  return new Elysia({ prefix }).get('/code', () => 'c')
}
