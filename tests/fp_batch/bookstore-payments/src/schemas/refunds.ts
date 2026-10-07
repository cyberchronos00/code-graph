import { t } from 'elysia'

export const refundBodySchema = t.Object({ order_id: t.String(), amount: t.Number(), note: t.Optional(t.String()) })
export const refundListQuery = t.Object({ status: t.String(), cursor: t.Optional(t.String()) })
export const refundParams = t.Object({ id: t.String() })
export const refundModels = {
  CreateRefundBody: refundBodySchema,
  ListRefundsQuery: refundListQuery,
  RefundParams: refundParams,
}
