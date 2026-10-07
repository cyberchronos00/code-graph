<?php

namespace App\Services;

use Illuminate\Support\Facades\Http;

class FeedPuller
{
    public function pull(string $feedUrl)
    {
        return Http::get($feedUrl)->json();
    }
}
