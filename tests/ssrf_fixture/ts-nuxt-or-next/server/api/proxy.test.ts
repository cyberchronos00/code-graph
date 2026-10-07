export async function testProxy(event: any) {
  const query = getQuery(event)
  return await $fetch(query.url as string)
}
