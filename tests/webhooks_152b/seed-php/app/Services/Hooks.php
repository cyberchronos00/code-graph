<?php

namespace App\Services;

use Illuminate\Support\Facades\Http;

class Hooks
{
    public function push($sub, array $payload): void
    {
        foreach ($sub->events as $name) {
            $body = json_encode(['event' => $name, 'data' => $payload]);
            $signature = hash_hmac('sha256', $body, $sub->secret);
            Http::withHeaders(['X-Bookstore-Signature' => $signature])->post($sub->url, $body);
        }
    }
}
