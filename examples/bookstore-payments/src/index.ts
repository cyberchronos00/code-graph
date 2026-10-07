import { Elysia } from 'elysia'
import { openapi } from '@elysiajs/openapi'
import { payments } from './routes/payments'
import { refunds } from './routes/refunds'

const app = new Elysia().use(openapi()).get('/', () => 'ok').use(payments).use(refunds).listen(3000)

export { app }
