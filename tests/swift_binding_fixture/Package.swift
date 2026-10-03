// swift-tools-version:5.9
import PackageDescription

let package = Package(
    name: "Binding",
    targets: [
        .target(name: "App", dependencies: ["Styleguide"]),
        .target(name: "Styleguide"),
        .testTarget(name: "AppTests", dependencies: ["App"]),
    ]
)
