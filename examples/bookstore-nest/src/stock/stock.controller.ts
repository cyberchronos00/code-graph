import { Body, Controller, Post, UseGuards } from '@nestjs/common';
import { StockService } from './stock.service';
import { ReserveStockDto } from './dto/reserve-stock.dto';
import { ApiKeyGuard } from '../common/api-key.guard';

@Controller('stock')
@UseGuards(ApiKeyGuard)
export class StockController {
  constructor(private readonly stock: StockService) {}

  @Post('reserve')
  reserve(@Body() dto: ReserveStockDto) {
    return this.stock.reserve(dto.sku, dto.quantity);
  }
}
