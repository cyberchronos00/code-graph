package com.example.pig

import android.content.Context

class SyncApiImpl(ctx: Context, private val tag: String = "x") : SyncApiImplBase(ctx), SyncApi {
    override fun clearCheckpoint() {}
}
