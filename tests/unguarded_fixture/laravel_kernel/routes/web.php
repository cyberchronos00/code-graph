<?php

use App\Http\Controllers\ParcelController;
use Illuminate\Support\Facades\Route;

Route::get('/storefront', [ParcelController::class, 'front']);
