package com.example.app

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent

class ExternalReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        println(intent.action)
    }
}
