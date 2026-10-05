package com.example.app

import android.app.Service
import android.content.Intent
import android.os.IBinder

class NotifyService : Service() {
    override fun onCreate() {
        super.onCreate()
    }

    override fun onBind(intent: Intent?): IBinder? {
        return null
    }
}
