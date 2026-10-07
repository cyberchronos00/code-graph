import { Elysia, t } from 'elysia'

export const foreign = new Elysia({ name: 'foreign-models' })
  .model({ secret: t.Object({ password: t.String() }) })

const orderModels = new Elysia({ name: 'order-models' })
  .model({ 'order.create': t.Object({ sku: t.String(), qty: t.Optional(t.Number()) }) })

export function orderRoutes() {
  return new Elysia({ prefix: '/orders' })
    .use(orderModels)
    .model({ order: t.Object({ id: t.String() }) })
    .decorate('orders', {})
    .state('seq', 0)
    .derive(() => ({ stamp: 1 }))
    .resolve(() => ({ ready: true }))
    .macro({})
    .onError(() => {})
    .post('/', ({ body }) => body, {
      body: t.Object({
        order_id: t.Number(),
        amount: t.Number(),
        note: t.Optional(t.String())
      }),
      query: t.Object({ cursor: t.Optional(t.String()) }),
      params: t.Object({ id: t.String() }),
      beforeHandle: function payGate({ set }) {
        set.status = 401
        return 'unauthorized'
      }
    })
    .post('/named', ({ body }) => body, { body: 'order.create' })
    .post('/missing', ({ body }) => body, { body: 'secret' })
    .get('/files/*', () => 'file')
    .get('/books/:isbn?', () => 'book')
}

export function mountOrders(app: Elysia) {
  return app.get('/mounted-orders', () => 'orders')
}
