<?php

namespace App\Services;

use GuzzleHttp\Client;

final class GuzzleOrders
{
    public function __construct(private Client $client) {}

    public function list(): void
    {
        $this->client->get('/catalog');
    }

    public function create(): void
    {
        $this->client->request('PUT', '/catalog', ['json' => ['sku' => 'book']]);
    }

    public function refund(): void
    {
        $payload = ['order_id' => '1', 'reason' => 'damaged'];
        $json = json_encode($payload, JSON_UNESCAPED_SLASHES);
        $this->client->request('POST', '/refunds', ['body' => $json]);
    }
}
