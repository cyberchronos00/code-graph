import { Module } from '@nestjs/common';
import { APP_GUARD, RouterModule } from '@nestjs/core';
import { ConfigModule } from '@nestjs/config';
import { TypeOrmModule } from '@nestjs/typeorm';
import { ScheduleModule } from '@nestjs/schedule';
import { EventEmitterModule } from '@nestjs/event-emitter';
import warehouseConfig from './config/warehouse.config';
import { ReportsModule } from './reports/reports.module';
import { StockModule } from './stock/stock.module';
import { OrdersModule } from './orders/orders.module';
import { AdminModule } from './admin/admin.module';
import { HealthController } from './common/health.controller';
import { ThrottleGuard } from './common/throttle.guard';
import { TasksService } from './tasks/tasks.service';
import { SyncWarehouseCommand } from './commands/sync-warehouse.command';

@Module({
  imports: [
    ConfigModule.forRoot({ load: [warehouseConfig] }),
    TypeOrmModule.forRoot({ type: 'postgres', url: process.env.DATABASE_URL }),
    ScheduleModule.forRoot(),
    EventEmitterModule.forRoot(),
    ReportsModule,
    StockModule,
    OrdersModule,
    AdminModule,
    RouterModule.register([{ path: 'admin', module: AdminModule }]),
  ],
  controllers: [HealthController],
  providers: [{ provide: APP_GUARD, useClass: ThrottleGuard }, TasksService, SyncWarehouseCommand],
})
export class AppModule {}
