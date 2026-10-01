import { prisma } from '@/lib/prisma';

/** Catalogue queries shared by pages and route handlers. */
export async function listBooks(q?: string) {
  return prisma.book.findMany({ where: q ? { title: { contains: q } } : undefined });
}

export async function getBook(id: number) {
  return prisma.book.findUnique({ where: { id } });
}

export async function deleteBook(id: number) {
  return prisma.book.delete({ where: { id } });
}
