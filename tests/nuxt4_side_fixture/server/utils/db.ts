import type { Recipe } from '../../shared/types/recipe'
import { slugify } from '../../shared/utils/slug'

export function listRecipes(): Recipe[] {
  return [{ id: 1, title: 'Lemon Tart', slug: slugify('Lemon Tart') }]
}
