'use server';

import { prisma } from '@/lib/prisma';
import { reserveStock } from '@/lib/warehouse';

/** Server action: put a book into the cart. */
export async function addToCart(bookId: number, quantity: number) {
  await reserveStock(`BOOK-${bookId}`, quantity);
  return prisma.cartItem.create({ data: { bookId, quantity } });
}
