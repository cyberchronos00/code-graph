<?php

namespace App\Http\Controllers;

use App\Models\Order;
use App\Services\PaymentsClient;
use App\Services\StockService;
use Illuminate\Http\Request;

class OrderController extends Controller
{
    public function __construct(private StockService $stock)
    {
    }

    public function store(Request $request)
    {
        $result = $this->stock->reserve($request->input('isbn'));
        $order = Order::create([
            'user_id' => $request->user()->id,
            'book_id' => $result['book_id'] ?? null,
            'book_isbn' => $request->input('isbn'),
            'total' => $result['price'] ?? 0,
        ]);
        $this->stock->recordSale((int) ($result['book_id'] ?? 0));
        event(new \App\Events\OrderShipped($order));

        return response()->json($order);
    }

    public function checkout(Order $order, PaymentsClient $payments)
    {
        return $payments->createPayment($order);
    }
}
