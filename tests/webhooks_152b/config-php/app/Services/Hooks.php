<?php

namespace App\Services;

use Illuminate\Support\Facades\Http;

class Hooks
{
    public function push($webhook, array $payload): void
    {
        $body = json_encode(['event' => $webhook->event_type, 'data' => $payload]);
        $signature = hash_hmac('sha256', $body, $webhook->secret);
        Http::withHeaders(['X-Bookstore-Signature' => $signature])->post($webhook->url, $body);
    }
}
