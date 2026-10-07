<?php

use Illuminate\Support\Facades\Route;

Route::post('/stripe/webhook', [\App\Http\Controllers\CashierWebhookController::class, 'handleWebhook']);
Route::post('/billing/webhook', [\App\Http\Controllers\BillingWebhookController::class, 'handleWebhook']);
Route::post('/invoices/paid', [\App\Http\Controllers\InvoiceActionsController::class, 'handleInvoicePaid']);
Route::webhooks('payments/hooks', 'payments');
Route::webhooks('checked/hooks', 'checked');
Route::webhooks('open/hooks', 'open');
Route::webhooks('blank/hooks', 'blank');
Route::webhooks('missing/hooks', 'missing-name');
Route::post('/webhooks/saleor', [\App\Http\Controllers\SaleorWebhookController::class, 'handle']);
Route::post('/hooks/loose', [\App\Http\Controllers\LooseHookController::class, 'handle']);
Route::post('/api/github/webhooks', [\App\Http\Controllers\GithubHookController::class, 'handle']);
