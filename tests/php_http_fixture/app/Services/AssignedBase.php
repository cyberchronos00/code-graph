<?php

namespace App\Services;

use Illuminate\Http\Client\Factory;

final class AssignedBase
{
    private string $base;

    public function __construct(private Factory $http, string $baseUrl)
    {
        $this->base = rtrim($baseUrl, '/');
    }

    public function ping(): void
    {
        $this->http->head($this->base . '/assigned');
    }
}
