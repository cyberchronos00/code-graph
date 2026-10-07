<?php

namespace App\Http\Controllers;

use App\Models\Order;
use Illuminate\Support\Facades\Gate;

class PolicyController extends Controller
{
    public function update(Order $order)
    {
        $this->authorize('update', $order);
        $order->save();
    }

    public function gate(Order $order)
    {
        Gate::authorize('refund', $order);
        $order->save();
    }

    public function denies(Order $order)
    {
        if (Gate::denies('update', $order)) {
            abort(403);
        }
        $order->save();
    }
}
