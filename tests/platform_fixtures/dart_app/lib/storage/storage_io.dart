import 'dart:io';

String save(String key) {
  File('${cachePath()}/$key').writeAsStringSync('');
  return key;
}

String? cachePath() => Directory.systemTemp.path;
