package com.acme.keyboard

import com.facebook.react.bridge.ReactApplicationContext

class KeyboardModule(context: ReactApplicationContext) : NativeKeyboardSpec(context) {
    override fun getName(): String = "Keyboard"

    override fun dismiss() {
    }

    override fun addListener(event: String) {}

    override fun removeListeners(count: Double) {}
}
