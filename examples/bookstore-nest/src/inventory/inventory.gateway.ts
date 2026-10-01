import { SubscribeMessage, WebSocketGateway } from '@nestjs/websockets';

@WebSocketGateway({ namespace: 'inventory' })
export class InventoryGateway {
  @SubscribeMessage('watch')
  watch(client: unknown, sku: string) {
    return { event: 'watching', data: sku };
  }

  broadcast(sku: string) {
    return sku;
  }
}
