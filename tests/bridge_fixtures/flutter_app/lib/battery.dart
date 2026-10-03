import 'package:flutter/services.dart';

const String chargingChannel = 'samples.flutter.dev/charging';

class Battery {
  static const MethodChannel _channel = MethodChannel('samples.flutter.dev/battery');
  final EventChannel _events = const EventChannel(chargingChannel);

  Future<int?> level() async {
    return _channel.invokeMethod<int>('getBatteryLevel');
  }

  Future<void> startCharging() async {
    await _channel.invokeMethod('startCharging');
  }

  Future<Map<String, dynamic>?> details() => _channel.invokeMapMethod<String, dynamic>('getDetails');

  Stream<dynamic> chargingEvents() => _events.receiveBroadcastStream();
}

Future<String?> deviceName() async {
  const platform = MethodChannel('samples.flutter.dev/device');
  return platform.invokeMethod<String>('getName');
}
