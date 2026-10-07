<?php

use App\Http\Controllers\Admin\BookController as AdminBookController;
use App\Http\Controllers\Admin\InventoryController;
use App\Http\Controllers\OrderController;
use App\Http\Controllers\ReportController;
use App\Http\Controllers\StockController;
use Illuminate\Support\Facades\Route;

Route::prefix('v1/{store}')->group(function () {
    Route::get('admin/reports/top', [ReportController::class, 'top']);
    Route::get('admin/reports/summary', [ReportController::class, 'summary']);
    Route::get('admin/reports/top/export.{format}', [ReportController::class, 'export']);
    Route::delete('admin/reports/{report}', [ReportController::class, 'destroy']);
    Route::get('admin/inventory', [InventoryController::class, 'index']);
    Route::post('orders', [OrderController::class, 'place']);
});

Route::prefix('v1')->group(function () {
    Route::post('stock/reserve', [StockController::class, 'reserve']);
    Route::middleware(['auth:api'])->group(function () {
        Route::post('orders', [OrderController::class, 'store']);
    });
    Route::post('admin/books', [AdminBookController::class, 'store']);
    Route::put('admin/books/{id}', [AdminBookController::class, 'update']);
    Route::middleware(['auth:sanctum'])->post('orders/{order}/refund', [\App\Http\Controllers\RefundController::class, 'store']);
    Route::post('warehouse/sync', \App\Http\Controllers\WarehouseSyncController::class);
});

Route::post('orders/{order}/checkout', [OrderController::class, 'checkout']);
