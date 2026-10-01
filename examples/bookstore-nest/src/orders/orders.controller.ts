import { Body, Controller, Post } from '@nestjs/common';
import { OrdersService } from './orders.service';
import { ReserveStockDto } from '../stock/dto/reserve-stock.dto';

@Controller('orders')
export class OrdersController {
  constructor(private readonly orders: OrdersService) {}

  @Post()
  create(@Body() dto: ReserveStockDto) {
    return this.orders.place(dto.sku, dto.quantity);
  }
}
