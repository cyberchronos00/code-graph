<?php

namespace App\Services;

use Illuminate\Http\Client\Factory;

final class VariableVerb
{
    public function __construct(private Factory $http) {}

    public function ping(string $method): void
    {
        $this->http->send($method, '/ping');
    }

    public function both(): void
    {
        $this->ping('GET');
        $this->ping('POST');
    }

    public function forward(string $method): void
    {
        $this->http->send($method, '/forward');
    }

    public function fanout(string $method): void
    {
        $this->http->send($method, '/shared');
    }
}
