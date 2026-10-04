import { createClient, ConnectRouter, Transport } from "@connectrpc/connect";
import { Dispatch } from "./gen/fleet_pb";

export async function reassign(transport: Transport) {
  const dispatch = createClient(Dispatch, transport);
  return dispatch.assign({ ride: "r1" });
}

function assignRide(req: unknown) {
  return { driver: "d2" };
}

export default (router: ConnectRouter) => router.service(Dispatch, { assign: assignRide });
