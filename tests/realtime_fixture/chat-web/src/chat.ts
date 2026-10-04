import { io } from "socket.io-client";

const socket = io("http://localhost:3000");
const admin = io("http://localhost:3000/admin");

export function sendMessage(text: string) {
  socket.emit("message:send", { room: "lobby", text });
}

export async function joinRoom(room: string) {
  await socket.timeout(5000).emitWithAck("room:join", room);
}

export function showJoined(id: string) {
  console.log(id);
}

export function watch() {
  socket.on("member:joined", showJoined);
  socket.on("order:shipped", (o) => console.log(o));
  socket.on("server:notice", (n) => console.log(n));
}

export function kick(id: string) {
  admin.emit("kick", id);
}

export function connectAdmin(): Promise<void> {
  return new Promise((resolve) => {
    admin
      .on("connect", () => resolve())
      .on("admin:stats", (s) => console.log(s))
      .on("admin:alert", (a) => console.log(a));
  });
}
