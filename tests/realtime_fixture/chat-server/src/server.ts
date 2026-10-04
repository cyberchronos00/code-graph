import { createServer } from "http";
import { Server } from "socket.io";
import { registerOrderHandlers } from "./orders";
import { Events } from "./events";

const httpServer = createServer();
const io = new Server(httpServer, { cors: { origin: "*" } });
const admin = io.of("/admin");

function authenticate(socket, next) {
  next();
}

admin.use(authenticate);

export function onSend(payload: { room: string; text: string }) {
  console.log(payload.text);
}

io.on("connection", (socket) => {
  socket.on("message:send", onSend);
  socket.on("room:join", (room: string, ack: (ok: boolean) => void) => {
    socket.join(room);
    socket.to(room).emit(Events.MemberJoined, socket.id);
    ack(true);
  });
  registerOrderHandlers(io, socket);
});

admin.on("connection", (socket) => {
  socket.on("kick", (id: string) => {
    io.emit("server:notice", `kicked ${id}`);
  });
});

export function notify(event: string, room: string, data: unknown) {
  io.to(room).emit(event, data);
}

export function orderShipped(id: string) {
  notify("order:shipped", `orders:${id}`, { id });
}

httpServer.listen(3000);
