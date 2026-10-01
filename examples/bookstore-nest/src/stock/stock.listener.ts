import { Injectable } from '@nestjs/common';
import { OnEvent } from '@nestjs/event-emitter';
import { AuditService } from '../audit/audit.service';

@Injectable()
export class StockListener {
  constructor(private readonly audit: AuditService) {}

  @OnEvent('stock.reserved')
  onReserved(payload: { sku: string }) {
    this.audit.record(`reserved ${payload.sku}`);
  }
}
