package demo

import java.net.DatagramPacket
import java.net.DatagramSocket
import java.net.InetAddress
import java.net.ServerSocket
import java.net.Socket

const val CONTROL_PORT = 4040

fun controlServer() {
    val server = ServerSocket(CONTROL_PORT)
    server.accept().close()
}

fun controlClient() {
    Socket("localhost", CONTROL_PORT).close()
}

fun sendMetric(data: ByteArray) {
    DatagramSocket().send(DatagramPacket(data, data.size, InetAddress.getByName("localhost"), 8125))
}
