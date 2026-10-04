import 'package:socket_io_client/socket_io_client.dart' as IO;

class ChatEvents {
  static const String message = 'chat:message';
}

class ChatService {
  IO.Socket? socket;

  void connect(String origin) {
    final s = IO.io(origin, IO.OptionBuilder().setTransports(['websocket']).build());
    socket = s;
    s.onConnect((_) => print('connected'));
    s.on('connect', (_) => print('again'));
    s.on(ChatEvents.message, _onMessage);
    s.on('room:joined', (data) {
      print(data);
    });
    final admin = IO.io('$origin/admin');
    admin.on('kick', _onKick);
  }

  void _onMessage(dynamic data) => print(data);

  void _onKick(dynamic data) => print(data);

  void send(String text) {
    socket?.emit('chat:send', {'text': text});
    socket?.emitWithAck('chat:history', {}, ack: (rows) => print(rows));
  }
}
