<?php

namespace App\Http\Controllers;

use Illuminate\Http\Request;
use Illuminate\Support\Facades\DB;

class ParcelController
{
    public function store(Request $request)
    {
        DB::table('parcels')->insert(['title' => $request->input('title')]);
        return response()->json(['ok' => true]);
    }

    public function front()
    {
        return DB::table('parcels')->get();
    }
}
