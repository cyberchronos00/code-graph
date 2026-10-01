<?php

namespace App\Services;

use App\Models\Book;
use App\Models\Warehouse\WarehouseItem;

class StockService
{
    public function reserve(string $isbn): array
    {
        $local = $this->reserveLocal($isbn);
        if ($local !== null) {
            return $local;
        }

        return $this->reserveFromWarehouse($isbn);
    }

    private function reserveLocal(string $isbn): ?array
    {
        $book = Book::where('isbn', $isbn)->where('is_active', true)->first();
        if (! $book || $book->isSoldOut()) {
            return null;
        }

        return ['success' => true, 'book_id' => $book->id, 'price' => $book->price];
    }

    private function reserveFromWarehouse(string $isbn): array
    {
        $item = WarehouseItem::where('isbn', $isbn)->where('is_active', 1)->first();
        if (! $item || $item->isSoldOut()) {
            return ['success' => false];
        }

        return ['success' => true, 'warehouse_id' => $item->id, 'price' => $item->price];
    }

    public function recordSale(int $bookId): void
    {
        Book::where('id', $bookId)->increment('sold_count');
    }
}
