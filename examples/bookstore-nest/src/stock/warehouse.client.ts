/** HTTP client of the external warehouse service. */
export class WarehouseClient {
  constructor(private readonly baseUrl: string) {}

  reserve(sku: string, quantity: number) {
    return fetch(`${this.baseUrl}/reservations`, { method: 'POST', body: JSON.stringify({ sku, quantity }) });
  }
}

export const WAREHOUSE_CLIENT = Symbol('WAREHOUSE_CLIENT');
