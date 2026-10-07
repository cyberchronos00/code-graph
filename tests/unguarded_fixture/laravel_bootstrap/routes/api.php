<?php

use App\Http\Controllers\ParcelController;
use Illuminate\Support\Facades\Route;

Route::post('/parcels', [ParcelController::class, 'store']);
