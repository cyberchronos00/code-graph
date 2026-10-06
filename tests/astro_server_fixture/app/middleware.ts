import { defineMiddleware, sequence } from 'astro:middleware'
import { currentUser } from './lib/auth'

const auth = defineMiddleware(async (context, next) => {
  if (!currentUser(context.cookies.get('t')?.value)) return context.redirect('/login')
  return next()
})

async function log(_context: unknown, next: () => Promise<Response>) {
  return next()
}

export const onRequest = sequence(auth, log)
