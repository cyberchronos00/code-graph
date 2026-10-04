<?php

use App\Http\Controllers\GithubWebhookController;
use App\Http\Controllers\StripeWebhookController;
use Illuminate\Support\Facades\Route;

Route::post('/webhooks/stripe', [StripeWebhookController::class, 'handle']);
Route::post('/webhooks/stripe-open', [StripeWebhookController::class, 'open']);
Route::post('/webhooks/github', [GithubWebhookController::class, 'handle']);
Route::post('/webhooks/gitlab', [GithubWebhookController::class, 'gitlab']);
