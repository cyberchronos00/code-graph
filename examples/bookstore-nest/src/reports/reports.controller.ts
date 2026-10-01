import { Controller, Delete, Get, Param, Query, UseInterceptors } from '@nestjs/common';
import { ReportsService } from './reports.service';
import { TopQueryDto } from './dto/top-query.dto';
import { AuditLogInterceptor } from '../common/audit-log.interceptor';

@Controller(':store/admin/reports')
export class ReportsController {
  constructor(private readonly reports: ReportsService) {}

  /** Top sellers for a store. */
  @Get('top')
  top(@Param('store') store: string, @Query() q: TopQueryDto) {
    return this.reports.top(store, q);
  }

  @Get('top/export.:format')
  export(@Param('store') store: string, @Param('format') format: string) {
    return this.reports.top(store, { mode: 'export' });
  }

  @Delete(':id')
  @UseInterceptors(AuditLogInterceptor)
  remove(@Param('id') id: string) {
    return this.reports.remove(Number(id));
  }
}
