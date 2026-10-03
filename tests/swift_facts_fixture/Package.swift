// swift-tools-version:5.9
import PackageDescription

let package = Package(
    name: "Facts",
    dependencies: [
        .package(url: "https://github.com/vapor/vapor.git", from: "4.0.0"),
        .package(url: "https://github.com/vapor/fluent.git", from: "4.0.0"),
        .package(url: "https://github.com/Moya/Moya.git", from: "15.0.0"),
    ],
    targets: [
        .target(name: "App", dependencies: [.product(name: "Vapor", package: "vapor"), .product(name: "Fluent", package: "fluent")]),
        .target(name: "Client", dependencies: ["Moya"]),
    ]
)
