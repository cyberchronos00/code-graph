import { Inject, Injectable } from '@nestjs/common';
import { InjectQueue } from '@nestjs/bullmq';
import { EventEmitter2 } from '@nestjs/event-emitter';
import { Queue } from 'bullmq';
import { WAREHOUSE_CLIENT, WarehouseClient } from './warehouse.client';

@Injectable()
export class StockService {
  constructor(
    @Inject(WAREHOUSE_CLIENT) private readonly warehouse: WarehouseClient,
    @InjectQueue('stock') private readonly stockQueue: Queue,
    private readonly events: EventEmitter2,
  ) {}

  async reserve(sku: string, quantity: number) {
    await this.warehouse.reserve(sku, quantity);
    this.events.emit('stock.reserved', { sku, quantity });
    await this.stockQueue.add('sync', { sku });
  }

  syncAll() {
    return this.stockQueue.add('sync', { all: true });
  }
}
