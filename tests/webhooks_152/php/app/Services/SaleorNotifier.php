<?php

namespace App\Services;

class SaleorNotifier
{
    public const SALEOR_SIGNATURE_HEADER = 'Saleor-Signature';

    public function send(string $url, string $body)
    {
        $sig = hash_hmac('sha256', $body, 'secret');

        return \Illuminate\Support\Facades\Http::withHeaders([
            self::SALEOR_SIGNATURE_HEADER => $sig,
        ])->post($url, ['event' => 'order.paid', 'body' => $body]);
    }
}
