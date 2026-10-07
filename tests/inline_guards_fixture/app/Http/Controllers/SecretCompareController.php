<?php

namespace App\Http\Controllers;

use Illuminate\Http\Request;

class SecretCompareController extends Controller
{
    public function store(Request $request)
    {
        if ($request->header('X-Sync-Secret') !== (string) config('bookstore.sync_secret')) {
            abort(401);
        }
    }
}
