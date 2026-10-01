import type { NextApiRequest, NextApiResponse } from 'next';

export default function wishlist(req: NextApiRequest, res: NextApiResponse) {
  const { method } = req;
  switch (method) {
    case 'GET':
      return res.json([]);
    case 'PUT':
      return res.status(204).end();
    default:
      res.setHeader('Allow', ['GET', 'PUT']);
      return res.status(405).end();
  }
}
