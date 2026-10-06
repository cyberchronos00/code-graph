<?php

namespace App\Services;

final class FallbackClient
{
    public function gone(): void
    {
        $base = config('services.missing.base_url') ?: env('FALLBACK_ONLY', 'https://fallback.bookstore.test/api');
        \Illuminate\Support\Facades\Http::delete(rtrim($base, '/') . '/gone');
    }
}
