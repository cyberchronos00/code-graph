package com.rnapp

import com.facebook.react.bridge.ReactApplicationContext
import com.facebook.react.bridge.ReactContextBaseJavaModule
import com.facebook.react.bridge.ReactMethod

class CalendarModule(context: ReactApplicationContext) : ReactContextBaseJavaModule(context) {
    override fun getName(): String = NAME

    @ReactMethod
    fun createEvent(name: String, location: String) {
        log(name)
    }

    @ReactMethod
    fun deleteEvent(id: String) {
    }

    @ReactMethod
    fun setBarColor(color: String) {
    }

    private fun log(s: String) {}

    companion object {
        const val NAME = "CalendarModule"
    }
}
