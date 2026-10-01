<?php

namespace App\Console\Commands;

use App\Models\Book;
use Illuminate\Console\Command;
use Illuminate\Support\Facades\DB;

/** One-off operator command: copy stock levels from the old warehouse database. */
class SyncWarehouseCommand extends Command
{
    protected $signature = 'bookstore:sync-warehouse';

    public function handle(): int
    {
        $rows = DB::connection(config('bookstore.warehouse_connection'))->table('warehouse_stock')->get();
        foreach ($rows as $row) {
            Book::where('isbn', $row->isbn)->update(['stock' => $row->stock]);
        }

        return 0;
    }
}
