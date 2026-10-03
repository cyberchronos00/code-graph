package com.example.pig

import android.content.Context

open class SyncApiImplBase(private val ctx: Context) {
    fun hashAll(path: String): Boolean = path.isNotEmpty()
}
