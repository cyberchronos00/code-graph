<?php

namespace App\Http\Controllers;

use App\Models\Order;

class DeepController extends Controller
{
    public function ok(Order $order)
    {
        $this->level1($order);
    }

    public function late(Order $order)
    {
        $this->a($order);
    }

    private function level1(Order $order): void
    {
        $this->level2($order);
    }

    private function level2(Order $order): void
    {
        $this->authorize('update', $order);
        $order->save();
    }

    private function a(Order $order): void
    {
        $this->b($order);
    }

    private function b(Order $order): void
    {
        $this->c($order);
    }

    private function c(Order $order): void
    {
        $this->authorize('nope', $order);
    }
}
