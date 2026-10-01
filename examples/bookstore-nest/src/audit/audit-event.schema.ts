import { Prop, Schema, SchemaFactory } from '@nestjs/mongoose';

@Schema({ collection: 'audit_events' })
export class AuditEvent {
  @Prop() action: string;
  @Prop() at: Date;
}
export const AuditEventSchema = SchemaFactory.createForClass(AuditEvent);
