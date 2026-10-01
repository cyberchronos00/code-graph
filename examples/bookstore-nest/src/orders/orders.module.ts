import { Module } from '@nestjs/common';
import { ClientsModule, Transport } from '@nestjs/microservices';
import { OrdersController } from './orders.controller';
import { OrdersService } from './orders.service';
import { StockModule } from '../stock/stock.module';
import { NotificationsModule } from '../notifications/notifications.module';
import { SHIPPING_SERVICE } from '../notifications/notifications.constants';

@Module({
  imports: [
    StockModule,
    NotificationsModule.forRoot({ sender: 'orders@bookstore.example' }),
    ClientsModule.register([{ name: SHIPPING_SERVICE, transport: Transport.TCP }]),
  ],
  controllers: [OrdersController],
  providers: [OrdersService],
})
export class OrdersModule {}
