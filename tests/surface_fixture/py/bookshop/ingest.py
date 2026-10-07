import socketserver


class LineHandler(socketserver.StreamRequestHandler):
    def handle(self):
        self.wfile.write(self.rfile.readline())


def serve():
    with socketserver.TCPServer(("0.0.0.0", 7100), LineHandler) as srv:
        srv.serve_forever()


def serve_local():
    with socketserver.TCPServer(("127.0.0.1", 7101), LineHandler) as srv:
        srv.serve_forever()
