<?php

namespace App\Http\Controllers;

use Illuminate\Http\Request;

class RoleController extends Controller
{
    public function update(Request $request)
    {
        abort_unless($request->user()->hasRole('admin'), 403);
    }
}
