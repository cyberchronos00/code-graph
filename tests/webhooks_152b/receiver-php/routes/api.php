<?php

use App\Http\Controllers\OrdersWebhookController;
use Illuminate\Support\Facades\Route;

Route::post('/webhooks/orders', [OrdersWebhookController::class, 'handle']);
Route::post('/webhooks/inventory', [OrdersWebhookController::class, 'inventory']);
