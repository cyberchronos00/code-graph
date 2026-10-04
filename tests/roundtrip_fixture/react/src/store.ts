export class LevelStore {
  level = 0

  save(v: number) {
    this.level = Math.min(Math.max(v, 0), 10)
  }
}

export const store = new LevelStore()
