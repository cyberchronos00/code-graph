<?php

use App\Http\Controllers\LonelyWebhookController;
use Illuminate\Support\Facades\Route;

Route::post('/webhooks/lonely', [LonelyWebhookController::class, 'handle']);
