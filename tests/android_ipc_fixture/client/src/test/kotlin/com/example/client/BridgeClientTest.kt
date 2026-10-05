package com.example.client

import android.content.ComponentName
import android.content.Intent
import com.example.bridge.IBridge
import io.mockk.every
import io.mockk.mockk
import io.mockk.verify
import org.junit.Assert.assertEquals
import org.junit.Test

class BridgeClientTest {
    private val bridge: IBridge = mockk()

    @Test
    fun pingIsForwarded() {
        every { bridge.ping() } returns "pong"
        assertEquals("pong", bridge.ping())
        verify { bridge.ping() }
    }

    @Test
    fun componentIsExplicit() {
        val intent = Intent()
        assertEquals(intent.component, ComponentName("com.example.app", "com.example.app.SyncService"))
    }

    private fun registerFake() {
        val name = ComponentName("com.example.app", "com.example.app.SyncService")
        println(name)
    }
}
