<?php

namespace App\Http\Controllers;

use App\Models\Order;
use Illuminate\Http\Request;

class ConditionalController extends Controller
{
    public function update(Request $request, Order $order)
    {
        if ($request->user()->cannot('orders.refund')) {
            abort(403);
        }
        $order->save();
    }
}
