package com.example.bookstore.ui

import com.example.bookstore.data.BookRepository

class BookViewModel(private val repository: BookRepository) {
    suspend fun load() = repository.catalog()

    suspend fun open(id: Int) = repository.details(id)

    suspend fun checkout(email: String, bookId: Int) = repository.buy(email, bookId)
}
