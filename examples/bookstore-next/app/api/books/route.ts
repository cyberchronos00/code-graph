import { NextRequest, NextResponse } from 'next/server';
import { listBooks } from '@/lib/books';
import { prisma } from '@/lib/prisma';
import { withSession } from '@/lib/auth';

export async function GET(req: NextRequest) {
  return NextResponse.json(await listBooks(req.nextUrl.searchParams.get('q') ?? undefined));
}

export const POST = withSession(async (req) => {
  const body = await req.json();
  return NextResponse.json(await prisma.book.create({ data: { title: body.title, priceCents: body.priceCents } }));
});
