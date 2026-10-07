<?php

namespace App\Services;

use Illuminate\Support\Facades\Http;

class Hooks
{
    public function push($subscription, array $payload): void
    {
        $body = json_encode(['event' => $subscription->event, 'data' => $payload]);
        $signature = hash_hmac('sha256', $body, $subscription->secret);
        Http::withHeaders(['X-Bookstore-Signature' => $signature])->post($subscription->url, $body);
    }
}
