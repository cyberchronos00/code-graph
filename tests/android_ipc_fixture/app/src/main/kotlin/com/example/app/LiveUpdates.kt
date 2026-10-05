package com.example.app

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter

const val ACTION_LIVE = "com.example.app.ACTION_LIVE"

class LiveUpdates(private val context: Context) {
    private val receiver = object : BroadcastReceiver() {
        override fun onReceive(context: Context, intent: Intent) {
            println(intent.action)
        }
    }

    fun start() {
        context.registerReceiver(receiver, IntentFilter(ACTION_LIVE))
        context.registerReceiver(receiver, IntentFilter(Intent.ACTION_SCREEN_ON))
    }

    fun publish() {
        val intent = Intent().apply {
            action = ACTION_LIVE
        }
        context.sendBroadcast(intent)
    }
}
