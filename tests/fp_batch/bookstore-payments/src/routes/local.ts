import { Elysia, t } from 'elysia'

const voucherBody = t.Object({ code: t.String(), cents: t.Number() })
const chained = voucherBody

export const vouchers = new Elysia({ prefix: '/vouchers' })
  .model({ Voucher: voucherBody, Chained: chained })
  .post('/', ({ body }) => body, { body: 'Voucher' })
  .post('/deep', ({ body }) => body, { body: 'Chained' })
  .post('/inline', ({ body }) => body, { body: t.Object({ sku: t.String() }) })

export const inlineMap = new Elysia({ prefix: '/inline-map' })
  .model({ Gift: t.Object({ to: t.String(), message: t.Optional(t.String()) }) })
  .post('/', ({ body }) => body, { body: 'Gift' })
