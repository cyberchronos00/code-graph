<?php

namespace App\Http\Controllers;

use App\Models\Order;
use Illuminate\Auth\Access\AuthorizationException;

class DenyController extends Controller
{
    public function update(Order $order)
    {
        $this->reject($order);
        $order->save();
    }

    private function reject(Order $order): void
    {
        throw new AuthorizationException();
    }
}
