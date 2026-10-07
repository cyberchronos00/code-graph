<?php

namespace App\Http\Controllers;

use Illuminate\Http\Request;
use Illuminate\Support\Facades\DB;

class StaffController
{
    public function restock(Request $request)
    {
        abort_unless(auth()->check(), 403);
        DB::table('stock')->insert(['title' => $request->input('title')]);
        return response()->json(['ok' => true]);
    }

    public function audit(Request $request)
    {
        DB::table('audit')->insert(['title' => $request->input('title')]);
        return response()->json(['ok' => true]);
    }
}
