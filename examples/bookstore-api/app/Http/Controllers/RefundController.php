<?php

namespace App\Http\Controllers;

use App\Models\Order;
use Illuminate\Http\Request;

class RefundController extends Controller
{
    public function store(Request $request, Order $order)
    {
        abort_unless($request->user()->hasPermission('orders.refund'), 403);
        Order::create([
            'user_id' => $request->user()->id,
            'total' => 0,
        ]);

        return response()->json($order);
    }
}
