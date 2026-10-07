<?php

namespace App\Http\Controllers;

use App\Models\Order;
use Illuminate\Http\Request;

class WarehouseSyncController extends Controller
{
    public function __invoke(Request $request)
    {
        $this->ensureValidSecret($request);
        Order::insert(['status' => 'synced']);

        return response()->json(['ok' => true]);
    }

    private function ensureValidSecret(Request $request): void
    {
        abort_unless(hash_equals((string) config('bookstore.sync_secret'), (string) $request->header('X-Sync-Secret')), 401);
    }
}
