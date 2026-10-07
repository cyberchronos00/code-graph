<?php

namespace App\Http\Controllers;

use App\Http\Requests\FilterRequest;
use App\Http\Requests\StoreOrderRequest;
use Illuminate\Http\Request;
use Illuminate\Support\Facades\Validator;

class OrderController
{
    public function store(StoreOrderRequest $request)
    {
        $data = $request->validated();

        return $data['book_id'];
    }

    public function update(Request $request)
    {
        $request->validate([
            'sku' => 'required|string',
            'items.*.id' => 'required|integer',
            'nickname' => 'sometimes|string',
        ]);
    }

    public function adjust(Request $request)
    {
        Validator::make($request->all(), [
            'qty' => 'required|integer',
            'status' => 'in:open,closed',
        ]);
    }

    public function coupon(Request $request)
    {
        return $request->validated('coupon');
    }

    public function index(FilterRequest $request)
    {
        return $request->validated();
    }

    public function loose(Request $request)
    {
        return $request->input('note');
    }

    public function plain(Request $request, string $id)
    {
        return $request->route('id');
    }
}
