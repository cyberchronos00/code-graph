export async function POST(request: Request) {
  const { link } = await request.json()
  const res = await fetch(link)
  return Response.json(await res.json())
}
