// swift-tools-version: 6.0
// Почти весь код приложения: собирается и тестируется `swift build` / `swift test` на macOS без Xcode.
import PackageDescription

let package = Package(
    name: "AntifraudKit",
    platforms: [.iOS(.v17), .macOS(.v14)],
    products: [
        .library(name: "AntifraudCore", targets: ["AntifraudCore"]),
        .library(name: "AntifraudAudio", targets: ["AntifraudAudio"]),
        .library(name: "AntifraudUI", targets: ["AntifraudUI"]),
    ],
    targets: [
        .target(name: "AntifraudCore"),
        .target(name: "AntifraudAudio"),
        .target(name: "AntifraudUI", dependencies: ["AntifraudCore", "AntifraudAudio"]),
        .testTarget(name: "AntifraudCoreTests", dependencies: ["AntifraudCore"]),
        .testTarget(name: "AntifraudAudioTests", dependencies: ["AntifraudAudio"]),
    ]
)
