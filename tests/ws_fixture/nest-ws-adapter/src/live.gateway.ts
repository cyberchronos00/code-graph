import { SubscribeMessage, WebSocketGateway } from "@nestjs/websockets";

@WebSocketGateway({ path: "/live" })
export class LiveGateway {
  handleConnection() {}

  @SubscribeMessage("chat")
  onChat() {
    return "pong";
  }
}
