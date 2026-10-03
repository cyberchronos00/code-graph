import Foundation

class SyncApiImpl: SyncApi {
  func hashAll(path: String) throws -> Bool {
    return !path.isEmpty
  }

  func clearCheckpoint() throws {}
}
