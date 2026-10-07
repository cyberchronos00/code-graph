<?php

namespace App\Services;

use App\Models\Book;
use App\Models\Shipment;
use Illuminate\Support\Facades\Config;
use Illuminate\Support\Facades\DB;

class Catalog
{
    public function inDefault(string $title)
    {
        return Book::where('title', $title)->get();
    }

    public function onChain(string $isbn)
    {
        return Book::on('warehouse')->where('isbn', $isbn)->get();
    }

    public function viaFacade(string $title)
    {
        return DB::connection('warehouse')->table('books')->where('title', $title)->get();
    }

    public function viaBuilderVariable(int $price)
    {
        $book = new Book();
        $book->setConnection('reporting');
        $book->update(['price' => $price]);
    }

    public function viaModelProperty(string $isbn)
    {
        return Shipment::where('isbn', $isbn)->first();
    }

    public function queryOverridesModel(string $isbn)
    {
        return Shipment::on('reporting')->where('isbn', $isbn)->get();
    }

    public function dynamicName(int $storeId)
    {
        return Book::on("legacy_{$storeId}")->where('store_id', $storeId)->get();
    }

    public function viaProvider(object $store)
    {
        return Book::on($this->legacyConnection($store))->where('price', 1)->get();
    }

    public function unknownName(string $name)
    {
        return Book::on($name)->where('price', 2)->get();
    }

    private function legacyConnection(object $store): string
    {
        $name = "legacy_{$store->id}";
        Config::set("database.connections.{$name}", $store->dbConfig());

        return $name;
    }
}
