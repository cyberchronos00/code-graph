'use client';

import { addToCart } from '@/app/actions';

export function CartButton({ bookId }: { bookId: number }) {
  const remove = () => fetch(`/api/books/${bookId}`, { method: 'DELETE' });
  return (
    <>
      <button onClick={() => addToCart(bookId, 1)}>Add to cart</button>
      <button onClick={remove}>Remove</button>
    </>
  );
}
