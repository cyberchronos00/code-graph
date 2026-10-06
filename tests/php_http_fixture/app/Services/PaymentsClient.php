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

    private function request(string $method, string $path, array $body = []): array
    {
        return $this->http->withBody(json_encode($body), 'application/json')
            ->send($method, rtrim($this->baseUrl, '/') . $path)->json();
    }
}
