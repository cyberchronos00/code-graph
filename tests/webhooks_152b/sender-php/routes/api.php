<?php

use App\Http\Controllers\PaymentsCallbackController;
use Illuminate\Support\Facades\Route;

Route::post('/webhooks/payments/{store}', [PaymentsCallbackController::class, 'handle'])->name('webhooks.payments');
Route::post('/webhooks/refunds/{store}', [PaymentsCallbackController::class, 'refund'])->name('webhooks.refunds');
Route::get('/books', [PaymentsCallbackController::class, 'index']);
