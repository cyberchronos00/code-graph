import { Injectable } from '@nestjs/common';
import { Cron, Interval } from '@nestjs/schedule';
import { StockService } from '../stock/stock.service';

@Injectable()
export class TasksService {
  constructor(private readonly stock: StockService) {}

  @Cron('0 3 * * *')
  nightlySync() {
    return this.stock.syncAll();
  }

  @Interval(60000)
  heartbeat() {
    return Date.now();
  }
}
