import { useEffect, useState } from 'react'

async function fetchResults(q: string): Promise<string[]> { return [q] }

export function Search({ q }: { q: string }) {
  const [results, setResults] = useState<string[]>([])
  useEffect(() => {
    const load = async () => {
      const found = await fetchResults(q)
      setResults(found)
    }
    load()
  }, [q])
  return <ul>{results.map((r) => <li key={r}>{r}</li>)}</ul>
}

export function SearchGuarded({ q }: { q: string }) {
  const [results, setResults] = useState<string[]>([])
  useEffect(() => {
    let cancelled = false
    fetchResults(q).then((found) => {
      if (!cancelled) setResults(found)
    })
    return () => { cancelled = true }
  }, [q])
  return <ul>{results.map((r) => <li key={r}>{r}</li>)}</ul>
}
