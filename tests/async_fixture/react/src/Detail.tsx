import { useEffect, useState } from 'react'

async function fetchTitle(q: string): Promise<string> { return q }

export function Detail({ seed }: { seed: string }) {
  const [title, setTitle] = useState('')
  useEffect(() => { setTitle(seed) }, [seed])
  const refresh = async () => {
    const t = await fetchTitle(seed)
    setTitle(t)
  }
  return <button onClick={refresh}>{title}</button>
}
