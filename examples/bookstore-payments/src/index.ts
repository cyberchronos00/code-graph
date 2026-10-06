import { Elysia } from 'elysia'
import { openapi } from '@elysiajs/openapi'
import { payments } from './routes/payments'

const app = new Elysia().use(openapi()).get('/', () => 'ok').use(payments).listen(3000)

export { app }
