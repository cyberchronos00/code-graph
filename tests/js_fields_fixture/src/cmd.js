export class Command {
  constructor(name) {
    this.name = name
    this.options = []
    this._done = false
  }

  option(o) {
    this.options.push(o)
    this._done = true
    return this.name
  }
}

export function make(kind) {
  if (kind === 'a') {
    return new Command('a')
  } else {
    return kind ? new Command('b') : null
  }
}
