<?php

use App\Http\Controllers\FetchController;
use Illuminate\Support\Facades\Route;

Route::get('/cover', [FetchController::class, 'cover']);
Route::get('/covers/{isbn}', [FetchController::class, 'byIsbn']);
Route::post('/notify', [FetchController::class, 'notify']);
Route::post('/import', [FetchController::class, 'import']);
Route::get('/mirror', [FetchController::class, 'mirror']);
Route::get('/lookup', [FetchController::class, 'lookup']);
Route::post('/raw', [FetchController::class, 'raw']);
