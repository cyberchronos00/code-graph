import type { NextApiRequest, NextApiResponse } from 'next';
import { prisma } from '@/lib/prisma';

/** Old pages-router endpoint kept for the mobile app. */
export default async function handler(req: NextApiRequest, res: NextApiResponse) {
  if (req.method === 'GET') {
    return res.json(await prisma.cartItem.findMany());
  }
  if (req.method === 'POST') {
    return res.json(await prisma.cartItem.create({ data: req.body }));
  }
  res.status(405).end();
}
