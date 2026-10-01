import { Command, CommandRunner } from 'nest-commander';
import { StockService } from '../stock/stock.service';

@Command({ name: 'sync-warehouse', description: 'Re-sync all stock with the warehouse' })
export class SyncWarehouseCommand extends CommandRunner {
  constructor(private readonly stock: StockService) {
    super();
  }

  async run(): Promise<void> {
    await this.stock.syncAll();
  }
}
