<?php

use App\Http\Controllers\PodcastController;
use Illuminate\Support\Facades\Route;

Route::post('/podcasts', [PodcastController::class, 'store']);
