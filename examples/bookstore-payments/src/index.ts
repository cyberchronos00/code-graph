import { Elysia } from 'elysia'

// Root routes only. Prefixed Elysia sub-apps (`.use()`, hooks as guards) are a separate change;
// these paths are indexed with the Elysia support that exists today.
const app = new Elysia()
app.post('/payments', () => ({ id: 'pay_1' }))
app.post('/payments/:id/cancel', () => ({ ok: true }))

export { app }
