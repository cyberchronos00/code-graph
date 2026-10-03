import 'package:flutter/services.dart';

class CounterModel {
  final _channel = const MethodChannel('example.dev/counter');
  int count = 0;

  CounterModel() {
    _channel.setMethodCallHandler(_handleMessage);
  }

  Future<dynamic> _handleMessage(MethodCall call) async {
    if (call.method == 'reportCounter') {
      count = call.arguments as int;
    }
  }
}

class Cell {
  static const MethodChannel _cell = MethodChannel("example.dev/cell");

  void listen() {
    _cell.setMethodCallHandler((call) async {
      switch (call.method) {
        case 'setCellNumber':
          return 1;
        case "reset":
          return 0;
      }
    });
  }
}
