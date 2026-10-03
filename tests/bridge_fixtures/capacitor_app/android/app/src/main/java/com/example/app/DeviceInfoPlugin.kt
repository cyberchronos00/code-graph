package com.example.app

import com.getcapacitor.Plugin
import com.getcapacitor.PluginCall
import com.getcapacitor.PluginMethod
import com.getcapacitor.annotation.CapacitorPlugin

@CapacitorPlugin
class DeviceInfo : Plugin() {
    @PluginMethod
    fun getInfo(call: PluginCall) {
        call.resolve()
    }
}
