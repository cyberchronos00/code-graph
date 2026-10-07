import { Elysia, t } from 'elysia'
import { refundModels } from '../schemas'

export const refunds = new Elysia({ prefix: '/refunds' })
  .model(refundModels)
  .post('/', ({ body }) => body, { body: 'CreateRefundBody' })
  .post('/again', ({ body }) => body, { body: t.Ref('CreateRefundBody') })
  .get('/', ({ query }) => query, { query: 'ListRefundsQuery' })
  .get('/:id', ({ params }) => params, { params: 'RefundParams' })
