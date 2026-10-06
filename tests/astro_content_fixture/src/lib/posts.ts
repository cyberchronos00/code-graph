import { getCollection } from 'astro:content'

export async function recentPosts() {
  return (await getCollection('blog')).slice(0, 3)
}
