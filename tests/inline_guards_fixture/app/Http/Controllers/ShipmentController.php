<?php

namespace App\Http\Controllers;

use Illuminate\Routing\Controllers\HasMiddleware;
use Illuminate\Routing\Controllers\Middleware;

class ShipmentController extends Controller implements HasMiddleware
{
    public static function middleware(): array
    {
        return [
            new Middleware('can:orders.update', only: ['update', 'destroy']),
            new Middleware('can:orders.view', except: ['update', 'destroy']),
        ];
    }

    public function index()
    {
    }

    public function store()
    {
    }

    public function show()
    {
    }

    public function update()
    {
    }

    public function destroy()
    {
    }
}
