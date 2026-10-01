<?php

use App\Http\Controllers\TaskController;
use App\Http\Controllers\WebhookController;
use Illuminate\Support\Facades\Route;

Route::middleware('auth:sanctum')->group(function () {
    Route::get('/boards/{board}/tasks', [TaskController::class, 'index'])->name('tasks.index');
    Route::patch('/tasks/{task}/move', [TaskController::class, 'move'])->name('tasks.move');
    Route::post('/tasks/{task}/comments', [TaskController::class, 'comment']);
});
Route::post('/webhooks/payments', [WebhookController::class, 'payment'])->middleware('verify.payment.webhook');
Route::get('/exports/{file}', [WebhookController::class, 'download'])->middleware('signed')->name('exports.download');
