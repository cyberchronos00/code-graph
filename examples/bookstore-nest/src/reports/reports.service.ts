import { Inject, Injectable } from '@nestjs/common';
import { REPORT_REPOSITORY, ReportRepository } from './report.repository';
import { TopQueryDto } from './dto/top-query.dto';

@Injectable()
export class ReportsService {
  constructor(@Inject(REPORT_REPOSITORY) private readonly reports: ReportRepository) {}

  top(store: string, q: TopQueryDto) {
    return this.reports.topSellers(store, q.date_from);
  }

  remove(id: number) {
    return this.reports.remove(id);
  }
}
