package com.example.bookstore.data

import com.example.bookstore.api.BookDto
import com.example.bookstore.api.BooksApi
import com.example.bookstore.api.OrderIn
import com.example.bookstore.api.OrderLine
import com.example.bookstore.api.OrderOut

class BookRepository(private val api: BooksApi) {
    suspend fun catalog(): List<BookDto> = api.books()

    suspend fun details(id: Int): BookDto = api.book(id)

    suspend fun buy(email: String, bookId: Int): OrderOut {
        val order = OrderIn(email, listOf(OrderLine(bookId, 1)))
        return api.placeOrder(order)
    }
}
