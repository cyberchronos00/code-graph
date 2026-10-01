import { Injectable } from '@nestjs/common';
import { InjectModel } from '@nestjs/mongoose';
import { Model } from 'mongoose';
import { AuditEvent } from './audit-event.schema';

@Injectable()
export class AuditService {
  constructor(@InjectModel(AuditEvent.name) private readonly events: Model<AuditEvent>) {}

  record(action: string) {
    return this.events.create({ action, at: new Date() });
  }
}
