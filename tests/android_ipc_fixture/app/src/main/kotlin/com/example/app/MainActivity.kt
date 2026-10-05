package com.example.app

import android.app.Activity
import android.content.Intent
import android.os.Bundle

const val ACTION_REFRESH = "com.example.app.ACTION_REFRESH"

class MainActivity : Activity() {
    private val scope = Scope()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        startSync()
    }

    fun startSync() {
        startService(Intent(this, SyncService::class.java))
    }

    fun refresh() {
        sendBroadcast(Intent(ACTION_REFRESH))
    }

    fun reopen() {
        val again = Intent(this, MainActivity::class.java)
        // the caller handles setResult / finish
        scope.launch { println("later") }
        startActivity(again)
    }

    fun share() {
        startActivity(Intent(Intent.ACTION_SEND).apply { type = "text/plain" })
    }
}

class Scope {
    fun launch(block: () -> Unit) = block()
}
