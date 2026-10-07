<?php

use App\Http\Controllers\GithubWebhookController;
use App\Http\Controllers\OrderController;
use Illuminate\Support\Facades\Route;

Route::post('/orders', [OrderController::class, 'store'])->middleware('auth:sanctum');
Route::delete('/admin/orders', [OrderController::class, 'purge']);
Route::get('/health', [OrderController::class, 'health']);
Route::post('/hooks/github', [GithubWebhookController::class, 'handle']);
Route::post('/hooks/github-open', [GithubWebhookController::class, 'open']);
