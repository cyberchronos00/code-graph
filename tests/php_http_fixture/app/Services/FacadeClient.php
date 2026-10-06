<?php

namespace App\Services;

final class FacadeClient
{
    public function health(): void
    {
        \Illuminate\Support\Facades\Http::withToken('t')->get(rtrim(config('services.payments.base_url'), '/') . '/health');
    }

    public function saved(): void
    {
        $req = \Illuminate\Support\Facades\Http::withToken('t');
        $req->delete('/saved');
    }
}
