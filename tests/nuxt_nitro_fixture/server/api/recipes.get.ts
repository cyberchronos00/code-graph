import { requireUser } from '../utils/auth'

export default defineEventHandler({
  onRequest: [requireUser],
  onBeforeResponse: (event, response) => {
    event.node.res.setHeader('x-recipes', String(response.body ? 1 : 0))
  },
  handler: async (event) => {
    return listRecipes(event)
  },
})

function listRecipes(event: unknown) {
  return [event]
}
