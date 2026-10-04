import type { Server, Socket } from "socket.io";

export function registerOrderHandlers(io: Server, socket: Socket) {
  socket.on("order:create", (order: { id: string; status: string }) => {
    io.emit(`order:${order.status}`, order);
  });
}
