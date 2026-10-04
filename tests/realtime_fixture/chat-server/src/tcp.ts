import { Socket } from "net";

// a plain TCP socket in a socket.io package: its events are not Socket.IO endpoints
export class TcpLink {
  constructor(private readonly socket: Socket) {
    this.socket.on("data", (b) => this.onData(b));
    this.socket.on("drain", () => undefined);
  }

  onData(b: Buffer) {
    this.socket.emit("parsed", b.length);
  }
}
