export function listPlants() {
  return [{ id: 1, name: 'Fern' }]
}

export function findPlant(id: string) {
  return listPlants().find(p => String(p.id) === id)
}

export function savePlant(name: string) {
  return { id: 2, name }
}
