import { NextResponse } from 'next/server';
import { listBooks } from '@/lib/books';

export async function GET(_req: Request, { params }: { params: Promise<{ q?: string[] }> }) {
  return NextResponse.json(await listBooks((await params).q?.join(' ')));
}
