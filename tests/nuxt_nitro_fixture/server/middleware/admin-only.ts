export default defineEventHandler((event) => {
  if (!event.path.startsWith('/api/admin')) return
  event.context.admin = true
})
