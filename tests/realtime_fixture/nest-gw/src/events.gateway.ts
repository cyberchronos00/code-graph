import { UseGuards } from "@nestjs/common";
import { ConnectedSocket, MessageBody, SubscribeMessage, WebSocketGateway, WebSocketServer } from "@nestjs/websockets";
import { Server, Socket } from "socket.io";
import { WsAuthGuard } from "./ws-auth.guard";

@WebSocketGateway({ namespace: "admin" })
export class EventsGateway {
  @WebSocketServer()
  server: Server;

  @UseGuards(WsAuthGuard)
  @SubscribeMessage("kick")
  handleKick(@MessageBody() id: string, @ConnectedSocket() client: Socket) {
    client.emit("kicked", id);
    this.server.emit("server:notice", id);
  }
}
