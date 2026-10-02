package com.example.bookstore.api

import retrofit2.Retrofit
import retrofit2.http.Body
import retrofit2.http.GET
import retrofit2.http.POST
import retrofit2.http.Path

data class BookDto(val id: Int, val title: String, val price: String)
data class OrderLine(val bookId: Int, val quantity: Int)
data class OrderIn(val customerEmail: String, val lines: List<OrderLine>)
data class OrderOut(val id: Int, val total: String)

interface BooksApi {
    @GET("api/books/")
    suspend fun books(): List<BookDto>

    @GET("api/books/{bookId}/")
    suspend fun book(@Path("bookId") bookId: Int): BookDto

    @POST("api/orders/")
    suspend fun placeOrder(@Body order: OrderIn): OrderOut
}

object ApiModule {
    // the Android emulator reaches the development server on the host as 10.0.2.2
    fun booksApi(): BooksApi = Retrofit.Builder()
        .baseUrl("http://10.0.2.2:8000/")
        .build()
        .create(BooksApi::class.java)
}
