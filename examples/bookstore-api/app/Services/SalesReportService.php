<?php

namespace App\Services;

use App\Models\Order;
use App\Models\Store;

class SalesReportService
{
    public function report(Store $store, array $filters): array
    {
        return $this->build($store, $filters);
    }

    private function build(Store $store, array $filters): array
    {
        $timezone = (string) ($filters['timezone'] ?? $store->getSetting('reports.timezone', 'UTC') ?: 'UTC');

        return Order::query()->where('placed_at', '>=', now($timezone)->startOfDay())->get()->all();
    }

    public function remove(int $id): void
    {
        Order::query()->whereKey($id)->delete();
    }
}
