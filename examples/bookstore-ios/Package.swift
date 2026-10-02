// swift-tools-version:5.9
import PackageDescription

let package = Package(
    name: "BookstoreApp",
    platforms: [.iOS(.v17), .macOS(.v14)],
    targets: [.executableTarget(name: "BookstoreApp", path: "BookstoreApp")]
)
