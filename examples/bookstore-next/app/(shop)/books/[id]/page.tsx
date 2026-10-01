import { getBook } from '@/lib/books';
import { CartButton } from '@/components/CartButton';

export async function generateMetadata({ params }: { params: Promise<{ id: string }> }) {
  const book = await getBook(Number((await params).id));
  return { title: book?.title };
}

export default async function BookPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const book = await getBook(Number(id));
  return <CartButton bookId={book.id} />;
}
