// swift-tools-version:5.10
import PackageDescription

let package = Package(
    name: "Lib",
    targets: [
        .target(name: "Lib"),
        .testTarget(name: "LibTests", dependencies: ["Lib"]),
    ]
)
