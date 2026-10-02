// swift-tools-version:5.9
import PackageDescription

let package = Package(
    name: "orders",
    platforms: [.macOS(.v13), .iOS(.v16)],
    dependencies: [.package(url: "https://github.com/vapor/vapor.git", from: "4.0.0")],
    targets: [
        .executableTarget(name: "App", dependencies: [.product(name: "Vapor", package: "vapor")]),
        .target(name: "Client"),
        .testTarget(name: "AppTests", dependencies: ["App"]),
    ]
)
