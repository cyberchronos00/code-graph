<?php

namespace App\Services;

use Illuminate\Support\Facades\Http;

class LedgerClient
{
    public function balance(): mixed
    {
        return Http::get('http://ledger.bookstore.example/api/balance')->json();
    }
}
