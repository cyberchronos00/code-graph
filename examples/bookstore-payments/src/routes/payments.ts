import { Elysia, t } from 'elysia'
import { signedRequests } from '../plugins/signed-requests'

function createPayment(_body: unknown) {
  return { id: 'pay_1' }
}
function getPayment(id: string) {
  return { id }
}
function cancelPayment(id: string) {
  return { id, cancelled: true }
}

export const payments = new Elysia({ prefix: '/payments' })
  .use(signedRequests)
  .model({ payment: t.Object({ id: t.String() }) })
  .post('/', ({ body }) => createPayment(body), { body: t.Object({ order_id: t.Number(), amount: t.Number() }) })
  .get('/:id', ({ params }) => getPayment(params.id))
  .post('/:id/cancel', ({ params }) => cancelPayment(params.id))
