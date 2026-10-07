<?php

namespace App\Services;

use App\Models\Book;
use App\Models\Store;
use Illuminate\Support\Facades\Config;

class ArchiveService
{
    public function legacyBooks(Store $store)
    {
        return Book::on($this->legacyConnection($store))->where('store_id', $store->id)->get();
    }

    private function legacyConnection(Store $store): string
    {
        $name = "legacy_{$store->id}";
        Config::set("database.connections.{$name}", $store->legacyDbConfig());

        return $name;
    }
}
