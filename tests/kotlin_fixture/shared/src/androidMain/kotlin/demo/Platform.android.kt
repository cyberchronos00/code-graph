package demo

actual fun platformName(): String = "Android " + sdkLevel()

fun sdkLevel(): Int = 35
