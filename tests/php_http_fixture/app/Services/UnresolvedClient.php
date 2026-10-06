<?php

namespace App\Services;

final class UnresolvedClient
{
    public function __construct(private string $baseUrl) {}

    public function go(): void
    {
        \Illuminate\Support\Facades\Http::post(rtrim($this->baseUrl, '/') . '/loose');
    }
}
