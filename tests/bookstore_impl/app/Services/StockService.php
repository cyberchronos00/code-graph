<?php

namespace App\Services;

use App\Models\Book;
use App\Models\Warehouse\WarehouseItem;

class StockService
{
    public function reserve(string $isbn, ?int $customerId = null): array
    {
        $local = $this->reserveLocal($isbn, $customerId);
        if ($local !== null) {
            return $local;
        }
        if (Book::where('isbn', $isbn)->whereNotNull('preorder_until')->exists()) {
            return ['success' => false];
        }

        return $this->reserveFromWarehouse($isbn);
    }

    private function reserveLocal(string $isbn, ?int $customerId): ?array
    {
        $book = Book::where('isbn', $isbn)->where('is_active', true)->first();
        if (! $book || $book->isSoldOut()) {
            return null;
        }
        if ($book->preorder_until !== null && $customerId === null) {
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
