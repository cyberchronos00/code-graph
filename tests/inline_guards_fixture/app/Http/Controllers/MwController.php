<?php

namespace App\Http\Controllers;

class MwController extends Controller
{
    public function __construct()
    {
        $this->middleware('can:orders.refund');
        $this->middleware('throttle:60,1');
        $this->middleware('role:admin')->only('destroy');
    }

    public function show()
    {
    }

    public function destroy()
    {
    }
}
