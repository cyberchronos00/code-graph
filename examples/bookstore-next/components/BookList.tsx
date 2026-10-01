'use client';

import useSWR from 'swr';

const fetcher = (url: string) => fetch(url).then((r) => r.json());

export function BookList({ initial }: { initial: unknown[] }) {
  const { data } = useSWR('/api/books', fetcher, { fallbackData: initial });
  return <ul>{(data as { id: number; title: string }[]).map((b) => <li key={b.id}>{b.title}</li>)}</ul>;
}
