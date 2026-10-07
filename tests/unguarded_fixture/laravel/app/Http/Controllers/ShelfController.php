<?php

namespace App\Http\Controllers;

use Illuminate\Http\Request;
use Illuminate\Support\Facades\DB;

class ShelfController
{
    public function index()
    {
        return DB::table('shelves')->get();
    }

    public function store(Request $request)
    {
        DB::table('shelves')->insert(['title' => $request->input('title')]);
        return response()->json(['ok' => true]);
    }
}
