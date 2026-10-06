export function BookCard({ book }: { book: { id: number; title: string } }) {
  return <article>{book.title}</article>;
}
