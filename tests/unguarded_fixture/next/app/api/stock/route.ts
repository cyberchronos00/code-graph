export async function GET() {
  const rows = await fetch('http://localhost:9000/stock')
  return Response.json(await rows.json())
}

export async function POST(req: Request) {
  const body = await req.json()
  return Response.json(body)
}
