<?php

namespace App\Http\Controllers;

use Illuminate\Http\Request;

class InvokeMwController extends Controller
{
    public function __construct()
    {
        $this->middleware('can:orders.refund')->only('__invoke');
        $this->middleware('role:admin')->except('__invoke');
    }

    public function __invoke(Request $request)
    {
    }
}
