import { NextRequest, NextResponse } from 'next/server';
import { deleteBook, getBook } from '@/lib/books';

export async function GET(_req: NextRequest, { params }: { params: Promise<{ id: string }> }) {
  return NextResponse.json(await getBook(Number((await params).id)));
}

async function remove(_req: NextRequest, { params }: { params: Promise<{ id: string }> }) {
  await deleteBook(Number((await params).id));
  return new NextResponse(null, { status: 204 });
}

export { remove as DELETE };
