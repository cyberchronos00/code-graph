import { NextRequest, NextResponse } from 'next/server';

export function middleware(req: NextRequest) {
  if (!req.cookies.get('session')) return NextResponse.redirect(new URL('/login', req.url));
  return NextResponse.next();
}

export const config = { matcher: ['/account/:path*', '/api/books/:path*'] };
