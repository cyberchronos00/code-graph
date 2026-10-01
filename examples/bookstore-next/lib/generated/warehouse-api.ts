/* Shaped like the output of an OpenAPI client generator (fictional, trimmed). */
export interface RequestParams {
  path: string;
  method: string;
  query?: Record<string, unknown>;
  body?: unknown;
}

export class WarehouseApi {
  constructor(private baseUrl: string = '') {}

  request = async <T>({ path, method, body }: RequestParams): Promise<T> => {
    const res = await fetch(`${this.baseUrl}${path}`, { method, body: body ? JSON.stringify(body) : undefined });
    return res.json() as Promise<T>;
  };

  warehouses = {
    list: () => this.request<unknown[]>({ path: `/v1/warehouses`, method: 'GET' }),
    update: (id: string, data: unknown) => this.request<void>({ path: `/v1/warehouses/${id}`, method: 'PUT', body: data }),
  };
}
