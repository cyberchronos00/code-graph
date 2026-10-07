import { loadFeed } from '../utils/feeds'

export default defineEventHandler(async (event) => {
  const body = await readBody(event)
  return await loadFeed(body.feed)
})
