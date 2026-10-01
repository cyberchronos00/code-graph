import { Processor, WorkerHost } from '@nestjs/bullmq';
import { Job } from 'bullmq';
import { InventoryGateway } from '../inventory/inventory.gateway';

@Processor('stock')
export class StockProcessor extends WorkerHost {
  constructor(private readonly gateway: InventoryGateway) {
    super();
  }

  async process(job: Job) {
    this.gateway.broadcast(job.data.sku);
  }
}
