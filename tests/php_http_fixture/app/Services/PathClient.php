<?php

namespace App\Services;

use Illuminate\Http\Client\Factory;

final class PathClient
{
    public function __construct(private Factory $http, private string $base) {}

    public function show(string $id): void
    {
        $url = sprintf('%s/orders/%s', rtrim($this->base, '/'), $id);
        $this->http->post($url);
    }

    public function interp(string $id): void
    {
        $this->http->get("{$this->base}/orders/{$id}/note");
    }

    public function joined(): void
    {
        $this->send('/notes');
    }

    private function send(string $path): void
    {
        $this->http->put(rtrim($this->base, '/') . $path);
    }
}
