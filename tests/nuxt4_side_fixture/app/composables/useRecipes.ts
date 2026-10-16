import type { Recipe } from '../../shared/types/recipe'

export function useRecipes() {
  const recipes: Recipe[] = []
  return { recipes }
}
