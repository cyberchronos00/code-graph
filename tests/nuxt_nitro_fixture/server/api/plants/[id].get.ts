import { findPlant } from '../../utils/store'

export default defineCachedEventHandler((event) => {
  return findPlant(getRouterParam(event, 'id') || '')
})
