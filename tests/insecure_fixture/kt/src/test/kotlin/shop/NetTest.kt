package shop

class NetTest {
    fun lenient() = okhttp3.OkHttpClient.Builder().hostnameVerifier { _, _ -> true }.build()
}
