<?php

namespace App\Services;

final class DefaultBaseClient
{
    public function __construct(private string $baseUrl) {}

    public function status(): void
    {
        \Illuminate\Support\Facades\Http::get(rtrim($this->baseUrl, '/') . '/status');
    }
}
