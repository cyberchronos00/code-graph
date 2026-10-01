import { stockLevel } from '@/lib/warehouse';

export default async function AccountPage() {
  const level = await stockLevel('BOOK-1');
  return <p>{JSON.stringify(level)}</p>;
}
