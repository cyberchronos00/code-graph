import { useState } from 'react'

export function Counter() {
  const [count, setCount] = useState(0)
  const [open, setOpen] = useState(false)
  const inc = () => setCount(count + 1)
  return <button onClick={() => setOpen(!open)} title={String(count)}>{count}</button>
}
