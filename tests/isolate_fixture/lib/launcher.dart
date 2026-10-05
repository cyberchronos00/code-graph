import 'dart:isolate';

import 'package:async/async.dart';

Future<void> launchTool() async {
  final port = ReceivePort();
  await Isolate.spawnUri(Uri.file('bin/tool.dart'), ['--x'], port.sendPort);
  await for (final msg in port) {
    print(msg);
  }
}

Future<void> pullReplies() async {
  final p = ReceivePort();
  await Isolate.spawn(replyService, p.sendPort);
  final events = StreamQueue<dynamic>(p);
  print(await events.next);
}

void replyService(SendPort out) {
  out.send('hello');
}
