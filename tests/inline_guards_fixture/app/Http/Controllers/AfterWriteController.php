<?php

namespace App\Http\Controllers;

use App\Models\Order;

class AfterWriteController extends Controller
{
    public function update(Order $order)
    {
        $order->save();
        $this->authorize('update', $order);
    }
}
