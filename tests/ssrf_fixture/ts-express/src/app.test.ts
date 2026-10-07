import axios from 'axios'

export async function testProxyCover(req: any, res: any) {
  const target = req.query.url
  await axios.get(target)
}
