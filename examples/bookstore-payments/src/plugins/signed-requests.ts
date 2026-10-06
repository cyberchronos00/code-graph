import { Elysia } from 'elysia'

function validSignature(_request: Request): boolean {
  return false
}

export const signedRequests = new Elysia({ name: 'signed-requests' })
  .onRequest(({ request, set }) => { if (!validSignature(request)) { set.status = 401; return 'unauthorized' } })
  .as('scoped')
