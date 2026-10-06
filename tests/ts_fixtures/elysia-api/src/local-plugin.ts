import { Elysia } from 'elysia'

export const localOnly = new Elysia({ name: 'local-only', prefix: '/local' })
  .onBeforeHandle(function localGate({ set }) {
    set.status = 401
    return 'no'
  })
  .get('/ping', () => 'local')
