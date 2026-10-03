<?php

namespace App\Http\Controllers;

use App\Models\Order;
use App\Models\Report;
use Illuminate\Support\Facades\DB;

class OrderController extends Controller
{
    public function index()
    {
        return DB::connection('pgsql')->table('orders')->get();
    }

    public function report()
    {
        return Report::query()->get();
    }
}
