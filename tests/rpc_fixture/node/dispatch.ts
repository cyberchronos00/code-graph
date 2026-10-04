import { credentials } from "@grpc/grpc-js";
import { DispatchClient } from "./gen/fleet_grpc_pb";

export class Fleet {
  private client: DispatchClient;

  constructor(address: string) {
    this.client = new DispatchClient(address, credentials.createInsecure());
  }

  assign(ride: unknown) {
    return this.client.assign(ride, () => undefined);
  }
}
