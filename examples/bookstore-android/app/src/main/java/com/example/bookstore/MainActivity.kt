package com.example.bookstore

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import com.example.bookstore.api.ApiModule
import com.example.bookstore.data.BookRepository
import com.example.bookstore.ui.BookViewModel
import com.example.bookstore.ui.BookstoreNavHost

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val viewModel = BookViewModel(BookRepository(ApiModule.booksApi()))
        setContent {
            BookstoreNavHost(rememberNavController(), viewModel)
        }
    }
}
