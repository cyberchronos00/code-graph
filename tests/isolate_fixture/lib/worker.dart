import 'dart:isolate';

import 'package:flutter/foundation.dart';

class HashWorker {
  RawReceivePort? _port;

  Future<void> start() async {
    final receivePort = ReceivePort();
    await Isolate.spawn(_entry, receivePort.sendPort);
    receivePort.listen(_onMessage);
  }

  void _onMessage(dynamic message) {
    print(message);
  }

  static void _entry(SendPort sendPort) {
    sendPort.send('ready');
  }

  Future<void> startRaw() async {
    final port = _port = RawReceivePort();
    port.handler = (Object? message) {
      print(message);
    };
    await Isolate.spawn(heavyTask, port.sendPort);
  }
}

void heavyTask(SendPort out) {
  final result = 42;
  Isolate.exit(out, result);
}

int parseAll(String text) => text.length;

Future<int> parseInBackground(String text) {
  return compute(parseAll, text);
}

Future<int> sumInBackground(List<int> xs) {
  return Isolate.run(() => sumAll(xs));
}

int sumAll(List<int> xs) => xs.fold(0, (a, b) => a + b);
