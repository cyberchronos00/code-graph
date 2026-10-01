import { Inject, Injectable } from '@nestjs/common';
import { ClientProxy } from '@nestjs/microservices';
import { StockService } from '../stock/stock.service';
import { NotificationsService } from '../notifications/notifications.service';
import { SHIPPING_SERVICE } from '../notifications/notifications.constants';

@Injectable()
export class OrdersService {
  constructor(
    private readonly stock: StockService,
    private readonly notifications: NotificationsService,
    @Inject(SHIPPING_SERVICE) private readonly shipping: ClientProxy,
  ) {}

  async place(sku: string, quantity: number) {
    await this.stock.reserve(sku, quantity);
    this.shipping.emit('order.placed', { sku, quantity });
    return this.notifications.orderPlaced(sku);
  }
}
