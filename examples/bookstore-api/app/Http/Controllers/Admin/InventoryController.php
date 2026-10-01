<?php

namespace App\Http\Controllers\Admin;

use App\Http\Controllers\Controller;
use App\Models\Book;
use App\Models\Store;
use App\Models\Warehouse\WarehouseItem;

class InventoryController extends Controller
{
    /** Stock list; stores still on the old warehouse read it from there. */
    public function index(string $store)
    {
        $current = Store::query()->firstOrFail();
        if (! $current->getSetting('features.new_inventory.enabled', false)) {
            return WarehouseItem::query()->where('is_active', 1)->get();   // gated under the new_inventory scenario
        }

        return Book::query()->where('is_active', true)->get();
    }
}
