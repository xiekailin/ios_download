// swift-tools-version: 6.1
import PackageDescription

let package = Package(
    name: "XDownloaderClient",
    platforms: [
        .iOS(.v17),
        .macOS(.v14),
    ],
    products: [
        .library(name: "AppCore", targets: ["AppCore"]),
        .library(name: "Networking", targets: ["Networking"]),
        .library(name: "Storage", targets: ["Storage"]),
        .library(name: "SharedUI", targets: ["SharedUI"]),
        .library(name: "PlatformAdapters", targets: ["PlatformAdapters"]),
    ],
    targets: [
        .target(name: "AppCore"),
        .target(name: "Networking", dependencies: ["AppCore"]),
        .target(name: "Storage", dependencies: ["AppCore"]),
        .target(name: "SharedUI", dependencies: ["AppCore"]),
        .target(name: "PlatformAdapters", dependencies: ["AppCore"]),
        .testTarget(name: "AppCoreTests", dependencies: ["AppCore"]),
    ]
)
