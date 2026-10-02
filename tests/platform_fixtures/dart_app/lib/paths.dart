import 'dart:io' show Platform;

import 'package:flutter/foundation.dart' show kIsWeb;

String? appPath() {
  final path = kIsWeb ? null : (Platform.isIOS ? iosPath() : androidPath());
  return path;
}

String iosPath() => '/var/mobile';

String androidPath() => '/data/data';

void openSettings() {
  if (Platform.isAndroid) {
    androidSettings();
  } else if (Platform.isIOS) {
    iosSettings();
  } else {
    desktopSettings();
  }
}

void androidSettings() {}

void iosSettings() {}

void desktopSettings() {}
