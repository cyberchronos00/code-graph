<?php

namespace App\Services;

use App\Models\Order;
use Illuminate\Http\Client\Factory;

final class PaymentsClient
{
    public function __construct(private Factory $http, private string $baseUrl) {}

    public function createPayment(Order $order): array
    {
        return $this->request('POST', '/payments', ['order_id' => $order->id, 'amount' => $order->total]);
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
