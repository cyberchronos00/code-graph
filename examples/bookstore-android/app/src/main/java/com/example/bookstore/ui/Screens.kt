package com.example.bookstore.ui

import androidx.compose.runtime.Composable
import androidx.navigation.NavHostController
import androidx.navigation.compose.NavHost
import androidx.navigation.compose.composable

@Composable
fun BookstoreNavHost(navController: NavHostController, viewModel: BookViewModel) {
    NavHost(navController, startDestination = "books") {
        composable("books") {
            BookListScreen(viewModel, onOpen = { id -> navController.navigate("books/$id") })
        }
        composable("books/{bookId}") { entry ->
            val id = entry.arguments?.getString("bookId")?.toInt() ?: 0
            BookDetailScreen(viewModel, id, onBuy = { navController.navigate("checkout/$id") })
        }
        composable("checkout/{bookId}") { entry ->
            CheckoutScreen(viewModel, entry.arguments?.getString("bookId")?.toInt() ?: 0)
        }
    }
}

@Composable
fun BookListScreen(viewModel: BookViewModel, onOpen: (Int) -> Unit) {
    LaunchedEffect(Unit) { viewModel.load() }
}

@Composable
fun BookDetailScreen(viewModel: BookViewModel, id: Int, onBuy: () -> Unit) {
    LaunchedEffect(id) { viewModel.open(id) }
}

@Composable
fun CheckoutScreen(viewModel: BookViewModel, bookId: Int) {
    Button(onClick = { scope.launch { viewModel.checkout("reader@example.com", bookId) } }) {
        Text("Buy")
    }
}
