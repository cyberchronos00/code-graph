import ky from 'ky';

/** Client of the separate warehouse service (examples/bookstore-express). */
const warehouse = ky.create({ prefixUrl: process.env.NEXT_PUBLIC_WAREHOUSE_URL });

export const stockLevel = (sku: string) => warehouse.get(`v1/stock/${sku}`).json();
export const reserveStock = (sku: string, quantity: number) => warehouse.post('v1/stock/reserve', { json: { sku, quantity } }).json();
