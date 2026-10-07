<?php

use App\Http\Controllers\BookController;
use Illuminate\Support\Facades\Route;

Route::patch('books/{book}', [BookController::class, 'updateValidated']);
Route::post('books/{book}/reviews', [BookController::class, 'addReview']);
