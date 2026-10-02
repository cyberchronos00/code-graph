package demo

class Gateway(private val client: HttpClient, private val http: OkHttpClient) {
    suspend fun stock(sku: String) = client.get("https://inventory.example.com/v2/stock/$sku")

    suspend fun ping(base: String) = client.post("$base/v1/ping")

    fun legacy(id: Int) {
        val req = Request.Builder().url("https://legacy.example.com/orders/$id").delete().build()
        http.newCall(req).execute()
    }
}
