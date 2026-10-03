import 'battery.dart';

Future<void> main() async {
  final b = Battery();
  print(await b.level());
  await b.startCharging();
  b.chargingEvents().listen(print);
  print(await deviceName());
}
