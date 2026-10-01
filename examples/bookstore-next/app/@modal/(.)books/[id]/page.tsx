import { getBook } from '@/lib/books';

/** Quick-view modal shown when a book is opened from the list (intercepts /books/[id]). */
export default async function BookModal({ params }: { params: Promise<{ id: string }> }) {
  const book = await getBook(Number((await params).id));
  return <dialog open>{book?.title}</dialog>;
}
