<?php

use App\Http\Controllers\ShelfController;
use App\Http\Controllers\StaffController;
use Illuminate\Support\Facades\Route;

Route::middleware('auth:sanctum')->group(function () {
    Route::post('/shelves', [ShelfController::class, 'store']);
    Route::get('/shelves', [ShelfController::class, 'index']);
});

Route::get('/pamphlets', [ShelfController::class, 'index']);
Route::post('/pamphlets', [ShelfController::class, 'store']);
Route::get('/ping', fn () => 'pong');
Route::post('/stock', [StaffController::class, 'restock']);
Route::get('/audit', [StaffController::class, 'audit']);
