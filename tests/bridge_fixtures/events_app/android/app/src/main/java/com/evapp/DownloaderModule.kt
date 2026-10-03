package com.evapp

import com.facebook.react.bridge.ReactApplicationContext
import com.facebook.react.bridge.ReactContextBaseJavaModule
import com.facebook.react.bridge.ReactMethod
import com.facebook.react.bridge.WritableMap
import com.facebook.react.modules.core.DeviceEventManagerModule

class DownloaderModule(private val ctx: ReactApplicationContext) : ReactContextBaseJavaModule(ctx) {
    override fun getName() = "Downloader"

    @ReactMethod
    fun start(url: String) {
        sendEvent("downloadProgress", null)
        finish()
    }

    private fun finish() {
        ctx.getJSModule(DeviceEventManagerModule.RCTDeviceEventEmitter::class.java).emit(EVT_DONE, null)
    }

    private fun sendEvent(eventName: String, params: WritableMap?) {
        ctx.getJSModule(DeviceEventManagerModule.RCTDeviceEventEmitter::class.java).emit(eventName, params)
    }

    companion object {
        const val EVT_DONE = "downloadDone"
    }
}
