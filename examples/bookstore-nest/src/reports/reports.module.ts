import { Module } from '@nestjs/common';
import { TypeOrmModule } from '@nestjs/typeorm';
import { MongooseModule } from '@nestjs/mongoose';
import { Order } from './order.entity';
import { ReportsController } from './reports.controller';
import { ReportsService } from './reports.service';
import { REPORT_REPOSITORY } from './report.repository';
import { TypeOrmReportRepository } from './typeorm-report.repository';
import { AuditService } from '../audit/audit.service';
import { AuditEvent, AuditEventSchema } from '../audit/audit-event.schema';

@Module({
  imports: [TypeOrmModule.forFeature([Order]), MongooseModule.forFeature([{ name: AuditEvent.name, schema: AuditEventSchema }])],
  controllers: [ReportsController],
  providers: [ReportsService, AuditService, { provide: REPORT_REPOSITORY, useClass: TypeOrmReportRepository }],
})
export class ReportsModule {}
