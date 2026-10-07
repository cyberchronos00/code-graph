<?php

use App\Http\Controllers\AuditController;
use Illuminate\Support\Facades\Route;

Route::post('audits', [AuditController::class, 'store']);
Route::get('audits', [AuditController::class, 'index']);
