<?php

use App\Http\Controllers\BookController;
use App\Http\Controllers\ShelfController;
use App\Http\Controllers\WishlistController;
use Illuminate\Support\Facades\Route;

Route::delete('wishlist/items', [WishlistController::class, 'remove']);
Route::get('books', [BookController::class, 'index']);
Route::get('books/browse', [BookController::class, 'browse']);
Route::get('shelves', [ShelfController::class, 'index']);
