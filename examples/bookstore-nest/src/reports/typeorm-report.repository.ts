import { Injectable } from '@nestjs/common';
import { InjectRepository } from '@nestjs/typeorm';
import { Repository } from 'typeorm';
import { Order } from './order.entity';
import { ReportRepository } from './report.repository';

@Injectable()
export class TypeOrmReportRepository implements ReportRepository {
  constructor(@InjectRepository(Order) private readonly orders: Repository<Order>) {}

  topSellers(store: string, from?: string) {
    return this.orders.find({ where: { store: { slug: store } }, order: { total: 'DESC' }, take: 10 });
  }

  async remove(id: number) {
    await this.orders.delete(id);
  }
}
