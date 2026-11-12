// swift-tools-version:5.9
import PackageDescription

let package = Package(
    name: "AcmeNotes",
    platforms: [.macOS(.v13)],
    dependencies: [
        .package(url: "https://example.com/vapor/vapor.git", from: "4.92.0"),
    ],
    targets: [
        .target(name: "AcmeNotes", dependencies: [.product(name: "Vapor", package: "vapor")]),
    ]
)
