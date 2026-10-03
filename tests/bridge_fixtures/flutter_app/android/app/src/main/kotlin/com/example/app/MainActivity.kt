package com.example.app

import io.flutter.embedding.android.FlutterActivity
import io.flutter.embedding.engine.FlutterEngine
import io.flutter.plugin.common.EventChannel
import io.flutter.plugin.common.MethodChannel

class MainActivity : FlutterActivity() {
    private val CHANNEL = "samples.flutter.dev/battery"

    override fun configureFlutterEngine(flutterEngine: FlutterEngine) {
        super.configureFlutterEngine(flutterEngine)
        MethodChannel(flutterEngine.dartExecutor.binaryMessenger, CHANNEL).setMethodCallHandler { call, result ->
            if (call.method == "getBatteryLevel") {
                result.success(batteryLevel())
            } else if (call.method == "startCharging") {
                result.success(null)
            } else {
                result.notImplemented()
            }
        }
        EventChannel(flutterEngine.dartExecutor.binaryMessenger, "samples.flutter.dev/charging").setStreamHandler(ChargingHandler())
        MethodChannel(flutterEngine.dartExecutor.binaryMessenger, "samples.flutter.dev/device").setMethodCallHandler(DeviceHandler())
    }

    private fun batteryLevel(): Int = 42
}

class DeviceHandler : MethodChannel.MethodCallHandler {
    override fun onMethodCall(call: MethodCall, result: MethodChannel.Result) {
        when (call.method) {
            "getName" -> result.success("pixel")
            else -> result.notImplemented()
        }
    }
}

class ChargingHandler : EventChannel.StreamHandler {
    override fun onListen(arguments: Any?, events: EventChannel.EventSink) {}
    override fun onCancel(arguments: Any?) {}
}
