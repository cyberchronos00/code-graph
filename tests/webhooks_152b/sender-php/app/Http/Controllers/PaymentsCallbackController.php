<?php

namespace App\Http\Controllers;

class PaymentsCallbackController
{
    public function handle($store)
    {
        return response()->json(['ok' => true]);
    }

    public function refund($store)
    {
        return response()->json(['ok' => true]);
    }

    public function index()
    {
        return response()->json([]);
    }
}
