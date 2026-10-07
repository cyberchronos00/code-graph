<?php

namespace App\Http\Controllers;

use App\Http\Requests\OpenRequest;
use App\Http\Requests\RefundRequest;
use App\Models\Order;

class FormController extends Controller
{
    public function update(RefundRequest $request, Order $order)
    {
        $order->save();
    }

    public function open(OpenRequest $request, Order $order)
    {
        $order->save();
    }
}
