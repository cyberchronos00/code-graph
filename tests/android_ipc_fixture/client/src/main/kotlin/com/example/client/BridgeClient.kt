package com.example.client

import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.content.ServiceConnection
import android.os.IBinder
import com.example.bridge.IBridge
import com.example.bridge.IBridgeCallback

private const val BRIDGE_SERVICE_CLASS = "com.example.app.BridgeService"

class BridgeClient(private val context: Context) : ServiceConnection {
    private var bridge: IBridge? = null

    private val callback = object : IBridgeCallback.Stub() {
        override fun onSynced(count: Int) {
            println(count)
        }
    }

    fun connect() {
        val intent = Intent().apply {
            component = ComponentName(
                "com.example.app",
                BRIDGE_SERVICE_CLASS,
            )
        }
        context.bindService(intent, this, Context.BIND_AUTO_CREATE)
    }

    override fun onServiceConnected(name: ComponentName?, binder: IBinder?) {
        val service = IBridge.Stub.asInterface(binder)
        bridge = service
        service.register(callback)
        println(service.ping())
    }

    fun requestSync() {
        bridge?.sync("manual")
    }

    override fun onServiceDisconnected(name: ComponentName?) {
        bridge = null
    }
}
