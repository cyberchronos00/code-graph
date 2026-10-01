<?php

namespace App\Http\Controllers;

use App\Services\StockService;
use Illuminate\Http\Request;

class StockController extends Controller
{
    public function __construct(private StockService $stock)
    {
    }

    public function reserve(Request $request)
    {
        return response()->json($this->stock->reserve($request->input('isbn')));
    }
}
