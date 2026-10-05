package com.example.app

import android.app.Service
import android.content.Intent
import android.os.IBinder
import android.os.RemoteCallbackList
import com.example.bridge.IBridge
import com.example.bridge.IBridgeCallback

class BridgeService : Service() {
    private val callbacks = RemoteCallbackList<IBridgeCallback>()

    private val binder = object : IBridge.Stub() {
        override fun ping(): String = "pong"

        override fun register(callback: IBridgeCallback?) {
            callbacks.register(callback)
        }

        override fun sync(reason: String?) {
            notifySynced(3)
        }
    }

    fun notifySynced(count: Int) {
        val n = callbacks.beginBroadcast()
        for (i in 0 until n) {
            callbacks.getBroadcastItem(i).onSynced(count)
        }
        callbacks.finishBroadcast()
    }

    override fun onBind(intent: Intent?): IBinder = binder
}
