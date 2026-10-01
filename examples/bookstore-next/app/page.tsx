import { listBooks } from '@/lib/books';
import { BookList } from '@/components/BookList';

export default async function HomePage() {
  const books = await listBooks();
  return <BookList initial={books} />;
}
