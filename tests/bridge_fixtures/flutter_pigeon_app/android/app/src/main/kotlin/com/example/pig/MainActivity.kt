package com.example.pig

import io.flutter.embedding.android.FlutterActivity
import io.flutter.embedding.engine.FlutterEngine
import io.flutter.plugin.common.MethodChannel

class MainActivity : FlutterActivity() {
    private lateinit var events: UploadEventsApi
    private lateinit var channel: MethodChannel

    override fun configureFlutterEngine(engine: FlutterEngine) {
        SyncApi.setUp(engine.dartExecutor.binaryMessenger, SyncApiImpl(this))
        events = UploadEventsApi(engine.dartExecutor.binaryMessenger)
        channel = MethodChannel(engine.dartExecutor.binaryMessenger, "example.dev/counter")
    }

    fun uploaded(n: Long) {
        events.onUpload(n) {}
    }

    fun report(n: Int) {
        channel.invokeMethod("reportCounter", n)
    }
}
