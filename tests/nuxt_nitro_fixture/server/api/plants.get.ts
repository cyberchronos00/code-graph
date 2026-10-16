import { listPlants } from '../utils/store'

export default defineEventHandler(() => {
  return listPlants()
})
