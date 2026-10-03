// swift-tools-version:5.9
import PackageDescription
let package = Package(name: "Repro", targets: [.target(name: "App"), .testTarget(name: "AppTests", dependencies: ["App"])])
