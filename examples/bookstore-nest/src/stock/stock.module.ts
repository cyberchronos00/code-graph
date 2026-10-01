import { Module } from '@nestjs/common';
import { ConfigService } from '@nestjs/config';
import { BullModule } from '@nestjs/bullmq';
import { StockController } from './stock.controller';
import { StockService } from './stock.service';
import { StockProcessor } from './stock.processor';
import { StockListener } from './stock.listener';
import { WAREHOUSE_CLIENT, WarehouseClient } from './warehouse.client';
import { InventoryGateway } from '../inventory/inventory.gateway';
import { InventoryController } from '../inventory/inventory.controller';
import { AuditService } from '../audit/audit.service';

@Module({
  imports: [BullModule.registerQueue({ name: 'stock' })],
  controllers: [StockController, InventoryController],
  providers: [
    StockService,
    StockProcessor,
    StockListener,
    InventoryGateway,
    AuditService,
    {
      provide: WAREHOUSE_CLIENT,
      useFactory: (config: ConfigService) => new WarehouseClient(config.get('warehouse.url')),
      inject: [ConfigService],
    },
  ],
  exports: [StockService],
})
export class StockModule {}
