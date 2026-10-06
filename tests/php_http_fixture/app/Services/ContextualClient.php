<?php

namespace App\Services;

final class ContextualClient
{
    public function __construct(private string $baseUrl) {}

    public function ping(): void
    {
        \Illuminate\Support\Facades\Http::baseUrl($this->baseUrl)->acceptJson()->get('/ping');
    }
}
