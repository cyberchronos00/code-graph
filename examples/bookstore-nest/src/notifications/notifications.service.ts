import { Inject, Injectable } from '@nestjs/common';
import { NOTIFY_OPTIONS } from './notifications.constants';

export interface NotifyOptions {
  sender: string;
}

@Injectable()
export class NotificationsService {
  constructor(@Inject(NOTIFY_OPTIONS) private readonly options: NotifyOptions) {}

  orderPlaced(sku: string) {
    return { from: this.options.sender, text: `order placed for ${sku}` };
  }
}
