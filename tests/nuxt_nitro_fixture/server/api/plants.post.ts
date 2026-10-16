import { savePlant } from '../utils/store'

export default defineEventHandler(async (event) => {
  const body = await readBody(event)
  return savePlant(body.name)
})
