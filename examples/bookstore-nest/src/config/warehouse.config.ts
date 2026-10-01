import { registerAs } from '@nestjs/config';

/** Settings of the external warehouse service. */
export default registerAs('warehouse', () => ({
  url: process.env.WAREHOUSE_URL,
  timeoutMs: parseInt(process.env.WAREHOUSE_TIMEOUT_MS ?? '5000', 10),
}));
