import 'storage/storage_stub.dart'
    if (dart.library.io) 'storage/storage_io.dart'
    if (dart.library.js_interop) 'storage/storage_web.dart';

void syncAll() {
  save('notes');
  cachePath();
}
