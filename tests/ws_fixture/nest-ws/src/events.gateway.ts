import { SubscribeMessage, WebSocketGateway } from "@nestjs/websockets";

const STATS_PATH = "/stats";

@WebSocketGateway({ path: "/events" })
export class EventsGateway {
  handleConnection() {}

  @SubscribeMessage("events")
  onEvent() {}
}

@WebSocketGateway(8081)
export class PortGateway {}

@WebSocketGateway(8080, { path: STATS_PATH })
export class StatsGateway {
  handleConnection() {}
}

declare const configPath: string;

@WebSocketGateway({ path: configPath })
export class ConfigGateway {
  handleConnection() {}
}
