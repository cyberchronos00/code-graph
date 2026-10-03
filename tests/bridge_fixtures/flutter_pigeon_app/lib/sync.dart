import 'package:flutter_pigeon_app/src/sync.g.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

final syncApiProvider = Provider<SyncApi>((ref) => SyncApi());

class SyncService {
  final SyncApi _api;

  SyncService(this._api);

  Future<bool> run(String path) => _api.hashAll(path);
}

Future<void> reset(dynamic ref) async {
  await ref.read(syncApiProvider).clearCheckpoint();
}

class UploadEventsHandler implements UploadEventsApi {
  @override
  void onUpload(int count) {}
}
