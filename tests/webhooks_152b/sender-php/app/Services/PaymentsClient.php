<?php

namespace App\Services;

use Illuminate\Support\Facades\Http;

class PaymentsClient
{
    public function createPayment($store, array $order): void
    {
        $url = config('app.url') . '/api/webhooks/payments/' . $store->slug;

        Http::post('https://pay.provider.test/v1/payments', [
            'amount' => $order['total'],
            'callback_url' => $url,
        ]);
    }

    public function createRefund($store, array $order): void
    {
        Http::post('https://pay.provider.test/v1/refunds', [
            'amount' => $order['total'],
            'notify_url' => route('webhooks.refunds', $store),
        ]);
    }

    public function createThirdParty(array $order): void
    {
        Http::post('https://pay.provider.test/v1/other', [
            'callback_url' => 'https://hooks.partner.test/api/webhooks/payments/x',
        ]);
    }

    public function createUnknownPath($store): void
    {
        Http::post('https://pay.provider.test/v1/unknown', [
            'callback_url' => config('app.url') . '/api/webhooks/not-a-route',
        ]);
    }

    public function registerHook(): void
    {
        $client = new \Provider\Client();
        $client->webhooks->create(['url' => url('/api/webhooks/payments/main')]);
    }
}
