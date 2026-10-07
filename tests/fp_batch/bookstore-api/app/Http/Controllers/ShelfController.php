<?php

namespace App\Http\Controllers;

use App\Models\Shelf;
use Illuminate\Http\Request;

class ShelfController
{
    public function index(Request $request)
    {
        return Shelf::query()->where('room', $request->query('room'))->paginate($request->integer('per_page', 15));
    }
}
