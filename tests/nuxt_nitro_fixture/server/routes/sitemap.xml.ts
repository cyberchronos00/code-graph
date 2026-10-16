import { listPlants } from '../utils/store'

export default defineEventHandler(() => listPlants().map(p => `/plants/${p.id}`).join('\n'))
