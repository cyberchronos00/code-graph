import Flutter
import UIKit

@main
@objc class AppDelegate: FlutterAppDelegate {
  var methodChannel: FlutterMethodChannel?

  override func application(
    _ application: UIApplication,
    didFinishLaunchingWithOptions launchOptions: [UIApplication.LaunchOptionsKey: Any]?
  ) -> Bool {
    let controller = window?.rootViewController as! FlutterViewController
    methodChannel = FlutterMethodChannel(name: "example.dev/counter", binaryMessenger: controller.binaryMessenger)
    return super.application(application, didFinishLaunchingWithOptions: launchOptions)
  }

  func report(count: Int) {
    methodChannel?.invokeMethod("reportCounter", arguments: count)
  }
}
