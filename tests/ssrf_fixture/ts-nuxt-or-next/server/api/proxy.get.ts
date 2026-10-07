export default defineEventHandler(async (event) => {
  const query = getQuery(event)
  const res = await $fetch(query.url as string)
  return res
})
