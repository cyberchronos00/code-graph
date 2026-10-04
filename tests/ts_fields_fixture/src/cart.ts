export class Item {
  constructor(public name: string, private readonly qty: number, label: string) {}
  get size() { return this.qty }
}

export class Cart {
  static LIMIT = 3
  items: Item[] = []
  total = 0
  #secret = 'x'
  onChange = () => { this.total++ }

  add(it: Item) {
    this.items.push(it)
    this.total += 1
    const n = this.items.length
    return n + this.#secret.length
  }
}

export function checkout(c: Cart) {
  c.total = 0
  console.log(c.items, new Item('a', 1, 'b').name)
  const d = new Cart()
  d.items.splice(0)
  delete (d as any).total
}

export class Registry {
  private cache: Record<string, number> = {}
  put(k: string, v: number) {
    this.cache[k] = v
    return this.cache[k]
  }
}
