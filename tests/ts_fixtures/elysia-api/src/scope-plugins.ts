import { Elysia } from 'elysia'

export const inner = new Elysia({ name: 'inner' })
  .onBeforeHandle({ as: 'scoped' }, function innerGuard() {
    return
  })
  .get('/inner', () => 'inner')

export const mid = new Elysia({ prefix: '/mid' })
  .use(inner)
  .get('/mine', () => 'mine')

export const globalGate = new Elysia({ name: 'global-gate' })
  .onBeforeHandle({ as: 'global' }, function traceRequests({ set }) {
    set.status = 403
    return 'forbidden'
  })

export const gated = new Elysia({ prefix: '/gated' })
  .use(globalGate)
  .get('/child', () => 'child')

export const afterGlobal = new Elysia({ prefix: '/after-global' }).get('/x', () => 'x')
