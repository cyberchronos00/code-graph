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
}
