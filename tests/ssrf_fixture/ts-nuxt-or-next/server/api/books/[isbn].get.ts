export default defineEventHandler(async (event) => {
  const isbn = getRouterParam(event, 'isbn')
  return await $fetch(`https://catalog.bookstore.test/books/${isbn}`)
})
