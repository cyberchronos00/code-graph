import { t } from 'elysia'

export const refundModels = {
  'refund.create': t.Object({
    order_id: t.String(),
    reason: t.Optional(t.String()),
  }),
}
