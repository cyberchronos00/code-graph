<?php

use App\Http\Controllers\GatewayWebhookController;
use App\Http\Controllers\ManagedWebhookController;
use Illuminate\Support\Facades\Route;

Route::post('/webhooks/gateway/{gateway}', [GatewayWebhookController::class, 'handle']);
Route::post('/webhooks/managed', [ManagedWebhookController::class, 'handle']);
