import 'package:pigeon/pigeon.dart';

@HostApi()
abstract class SyncApi {
  bool hashAll(String path);

  void clearCheckpoint();
}

@FlutterApi()
abstract class UploadEventsApi {
  void onUpload(int count);
}
