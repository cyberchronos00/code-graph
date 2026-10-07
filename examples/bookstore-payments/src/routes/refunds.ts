import { Elysia, t } from 'elysia'
import { refundModels } from './refund-models'

const refundModelsPlugin = new Elysia({ name: 'refund-models' }).model(refundModels)

export const refunds = new Elysia({ prefix: '/refunds' })
  .use(refundModelsPlugin)
  .model({ 'refund.note': t.Object({ text: t.String() }) })
  .post('/', ({ body }) => ({ id: 'r1', ...body }), { body: 'refund.create' })
  .post('/note', ({ body }) => body, { body: t.Ref('refund.note') })
