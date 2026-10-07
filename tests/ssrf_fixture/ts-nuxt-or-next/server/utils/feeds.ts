export async function loadFeed(feedUrl: string) {
  const res = await fetch(feedUrl)
  return res.json()
}
