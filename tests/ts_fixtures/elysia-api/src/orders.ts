import { Elysia, t } from 'elysia'

export function orderRoutes() {
  return new Elysia({ prefix: '/orders' })
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
    .get('/files/*', () => 'file')
    .get('/books/:isbn?', () => 'book')
}

export function mountOrders(app: Elysia) {
  return app.get('/mounted-orders', () => 'orders')
}
