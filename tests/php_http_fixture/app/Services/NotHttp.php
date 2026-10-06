<?php

namespace App\Services;

use Illuminate\Http\Request;

/** Calls that must not become http: client endpoints. */
final class NotHttp
{
    public function go(Repository $repo, Mailer $mailer, Bag $items, Request $request): void
    {
        $repo->send('/not-a-client/repo');
        $mailer->post('/not-a-client/mail');
        $items->get('/not-a-client/item');
        $request->get('/not-a-client/req');
        \Illuminate\Support\Facades\Cache::get('not-a-client');
        config()->get('services.payments.base_url');
    }
}

final class Repository
{
    public function send(string $path): void
    {
    }
}

final class Mailer
{
    public function post(string $path): void
    {
    }
}

final class Bag
{
    public function get(string $key): void
    {
    }
}
