import { listRecipes } from '../utils/db'

export default function handler() {
  return listRecipes()
}
