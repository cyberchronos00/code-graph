<?php

use App\Http\Controllers\OrderController;
use Illuminate\Support\Facades\Route;

Route::post('/orders/{id}/ship', [OrderController::class, 'ship']);
Route::post('/orders/{id}/receipt', [OrderController::class, 'receipt']);
Route::post('/orders/{id}/archive', [OrderController::class, 'archive']);
