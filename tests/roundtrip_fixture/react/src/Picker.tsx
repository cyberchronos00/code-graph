import { useState } from 'react'
import { store } from './store'

export function Picker() {
  const [value, setValue] = useState(store.level)
  return <input type="range" min={0} max={100} value={value} onChange={(e) => { setValue(+e.target.value); store.save(+e.target.value) }} />
}
