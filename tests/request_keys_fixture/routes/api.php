<?php

use App\Http\Controllers\OrderController;
use Illuminate\Support\Facades\Route;

Route::post('orders', [OrderController::class, 'store']);
Route::put('orders/{id}', [OrderController::class, 'update']);
Route::patch('orders/{id}', [OrderController::class, 'adjust']);
Route::post('coupons', [OrderController::class, 'coupon']);
Route::get('orders', [OrderController::class, 'index']);
Route::post('loose', [OrderController::class, 'loose']);
Route::get('plain/{id}', [OrderController::class, 'plain']);
Route::post('cased', [OrderController::class, 'store'])->middleware('ConvertCase');
