// swift-tools-version:5.7
import PackageDescription

let package = Package(name: "XPCFixture", targets: [.executableTarget(name: "Helper", path: "Helper"),
                                                   .target(name: "App", path: "App")])
