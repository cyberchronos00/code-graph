import { Elysia } from 'elysia'
import { localOnly } from './local-plugin'
import { afterGlobal, gated, mid } from './scope-plugins'
import { mountOrders, orderRoutes } from './orders'
import { compose, slashed } from './compose'

function traceId() {
  return 'trace'
}

const app = new Elysia()
  .get('/before', () => 'before')
  .onRequest(function earlyRequest({ set }) {
    set.status = 401
    return 'unauthorized'
  })
  .get('/after-request', () => 'after')
  .onBeforeHandle(traceId)
  .get('/after-handle', () => 'handle')
  .get('/open', () => 'open')
  .guard({ beforeHandle: function guardAll() { return } })
  .get('/closed', () => 'closed')
  .guard({ beforeHandle: function onlyInside() { return } }, (app) => app.get('/in', () => 'in'))
  .get('/out', () => 'out')
  .use(localOnly)
  .get('/parent', () => 'parent')
  .use(mid)
  .get('/top', () => 'top')
  .use(gated)
  .use(afterGlobal)
  .use(orderRoutes())
  .use(mountOrders)
  .group('/v1', (app) => app.get('/orders/:id', () => 'order'))
  .use(import('./lazy-shelf'))
  .use(compose)
  .use(slashed)

app.listen(3000)

export default app
