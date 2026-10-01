import { Controller } from '@nestjs/common';
import { EventPattern, MessagePattern } from '@nestjs/microservices';
import { StockService } from '../stock/stock.service';

/** Handlers for messages from other services (no HTTP routes). */
@Controller()
export class InventoryController {
  constructor(private readonly stock: StockService) {}

  @MessagePattern({ cmd: 'inventory.check' })
  check(sku: string) {
    return { sku, available: true };
  }

  @EventPattern('order.shipped')
  shipped(data: { sku: string; quantity: number }) {
    return this.stock.reserve(data.sku, -data.quantity);
  }
}
