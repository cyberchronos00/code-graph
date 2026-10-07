package shop

import io.grpc.ManagedChannel
import io.grpc.ManagedChannelBuilder
import javax.net.ssl.HostnameVerifier
import javax.net.ssl.SSLSession
import javax.net.ssl.X509TrustManager
import java.security.cert.X509Certificate
import okhttp3.OkHttpClient

class TrustEverything : X509TrustManager {
    override fun checkClientTrusted(chain: Array<X509Certificate>, authType: String) {}
    override fun checkServerTrusted(chain: Array<X509Certificate>, authType: String) {}
    override fun getAcceptedIssuers(): Array<X509Certificate> = arrayOf()
}

class TrustPinned(private val pinned: X509Certificate) : X509TrustManager {
    override fun checkClientTrusted(chain: Array<X509Certificate>, authType: String) {}
    override fun checkServerTrusted(chain: Array<X509Certificate>, authType: String) {
        if (chain[0] != pinned) throw IllegalStateException("unexpected certificate")
    }
    override fun getAcceptedIssuers(): Array<X509Certificate> = arrayOf(pinned)
}

class AnyHost : HostnameVerifier {
    override fun verify(hostname: String, session: SSLSession): Boolean = true
}

fun lenientClient(): OkHttpClient =
    OkHttpClient.Builder().hostnameVerifier { _, _ -> true }.build()

fun inventoryChannel(): ManagedChannel =
    ManagedChannelBuilder.forAddress("inventory.bookstore.example", 50051)
        .usePlaintext()
        .build()

fun localInventoryChannel(): ManagedChannel =
    ManagedChannelBuilder.forAddress("localhost", 50051).usePlaintext().build()

fun tlsInventoryChannel(): ManagedChannel =
    ManagedChannelBuilder.forAddress("inventory.bookstore.example", 443).useTransportSecurity().build()

fun openStockSession(jsch: com.jcraft.jsch.JSch) {
    val session = jsch.getSession("sync", "stock.bookstore.example", 22)
    session.setConfig("StrictHostKeyChecking", "no")
    session.connect()
}
