import { DynamicModule, Module } from '@nestjs/common';
import { NOTIFY_OPTIONS } from './notifications.constants';
import { NotificationsService, NotifyOptions } from './notifications.service';

/** Dynamic module: its providers are only visible in forRoot(). */
@Module({})
export class NotificationsModule {
  static forRoot(options: NotifyOptions): DynamicModule {
    return {
      module: NotificationsModule,
      providers: [{ provide: NOTIFY_OPTIONS, useValue: options }, NotificationsService],
      exports: [NotificationsService],
    };
  }
}
