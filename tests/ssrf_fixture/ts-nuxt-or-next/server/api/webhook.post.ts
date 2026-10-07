const ALLOWED_HOSTS = ['hooks.bookstore.test']

export default defineEventHandler(async (event) => {
  const body = await readBody(event)
  const target = new URL(body.callback)
  if (!ALLOWED_HOSTS.includes(target.hostname)) {
    throw createError({ statusCode: 400 })
  }
  return await $fetch(target.href, { method: 'POST' })
})
