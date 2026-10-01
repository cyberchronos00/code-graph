import { NextRequest, NextResponse } from 'next/server';

type Handler = (req: NextRequest, ctx: { params: Promise<Record<string, string>> }) => Promise<Response>;

/** Wraps a route handler with a session check. */
export function withSession(handler: Handler): Handler {
  return async (req, ctx) => {
    if (!req.cookies.get('session')) return NextResponse.json({ error: 'unauthorized' }, { status: 401 });
    return handler(req, ctx);
  };
}
