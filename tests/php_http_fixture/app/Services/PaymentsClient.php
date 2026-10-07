<?php

namespace App\Services;

use Illuminate\Http\Client\Factory;

final class PaymentsClient
{
    public function __construct(private Factory $http, private string $baseUrl) {}

    public function createPayment(int $id, int $total): array
    {
        return $this->request('POST', '/payments', ['order_id' => $id, 'amount' => $total]);
    }

    public function cancelPayment(string $id): array
    {
        return $this->request('POST', "/payments/{$id}/cancel");
    }

    public function createRefund(string $orderId): array
    {
        return $this->send('POST', '/refunds', ['order_id' => $orderId, 'reason' => 'damaged']);
    }

    private function send(string $method, string $path, array $payload): array
    {
        $json = json_encode($payload);

        return $this->http->withBody($json, 'application/json')
            ->send($method, rtrim($this->baseUrl, '/') . $path)->json();
    }

    public function pingMissing(): array
    {
        return $this->http->withBody(json_encode($notSet), 'application/json')
            ->send('POST', rtrim($this->baseUrl, '/') . '/ping-missing')->json();
    }

    public function pingDouble(): array
    {
        $payload = ['order_id' => 1];
        $json = json_encode(json_encode($payload));

        return $this->http->withBody($json, 'application/json')
            ->send('POST', rtrim($this->baseUrl, '/') . '/ping-double')->json();
    }

    private function request(string $method, string $path, array $body = []): array
    {
        return $this->http->withBody(json_encode($body), 'application/json')
            ->send($method, rtrim($this->baseUrl, '/') . $path)->json();
    }
}
