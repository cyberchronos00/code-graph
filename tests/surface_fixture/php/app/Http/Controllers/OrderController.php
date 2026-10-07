<?php

namespace App\Http\Controllers;

use App\Models\Order;
use App\Services\LedgerClient;
use App\Services\Mailers;
use Illuminate\Http\Request;

class OrderController extends Controller
{
    public function store(Request $request, Mailers $mail, LedgerClient $ledger)
    {
        Order::create(['title' => $request->input('title')]);
        $ledger->balance();
        $mail->receipt($request->input('body'));

        return response()->json(['ok' => true]);
    }

    public function purge()
    {
        Order::query()->delete();

        return response()->json(['purged' => true]);
    }

    public function health()
    {
        return response()->json(['ok' => true]);
    }
}
