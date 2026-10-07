<?php

namespace App\Http\Controllers;

use App\Http\Concerns\ManagesOrders;
use App\Models\Order;

class TraitController extends Controller
{
    use ManagesOrders;

    public function update(Order $order)
    {
        $this->assertCanManage($order);
        $order->save();
    }

    public function plain(Order $order)
    {
        $this->assertOwns($order);
        $order->save();
    }
}
