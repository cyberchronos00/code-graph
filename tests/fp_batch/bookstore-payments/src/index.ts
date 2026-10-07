import { Elysia } from 'elysia'
import { refunds } from './routes/refunds'
import { vouchers, inlineMap } from './routes/local'

export const app = new Elysia().use(refunds).use(vouchers).use(inlineMap)
