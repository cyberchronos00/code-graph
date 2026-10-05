package com.example.app

import android.app.PendingIntent
import androidx.core.app.PendingIntentCompat
import android.app.Service
import android.content.Intent
import android.os.IBinder

class SyncService : Service() {
    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        val open = PendingIntent.getActivity(this, 0, Intent(this, MainActivity::class.java), PendingIntent.FLAG_IMMUTABLE)
        println(open)
        val notify = PendingIntentCompat.getService(
            this,
            1,
            Intent(this, NotifyService::class.java),
            PendingIntent.FLAG_IMMUTABLE,
        )
        println(notify)
        return START_NOT_STICKY
    }

    override fun onBind(intent: Intent?): IBinder? = null
}
