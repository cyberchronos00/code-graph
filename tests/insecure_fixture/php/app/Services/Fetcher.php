<?php

namespace App\Services;

use GuzzleHttp\Client;
use Illuminate\Support\Facades\Http;

class Fetcher
{
    public function viaCurl(): string
    {
        $ch = curl_init('https://prices.bookstore.example/today');
        curl_setopt($ch, CURLOPT_SSL_VERIFYPEER, false);
        curl_setopt($ch, CURLOPT_SSL_VERIFYHOST, 0);
        return (string) curl_exec($ch);
    }

    public function viaCurlChecked(): string
    {
        $ch = curl_init('https://prices.bookstore.example/today');
        curl_setopt($ch, CURLOPT_SSL_VERIFYPEER, true);
        curl_setopt($ch, CURLOPT_SSL_VERIFYHOST, 2);
        return (string) curl_exec($ch);
    }

    public function viaGuzzle(): string
    {
        $client = new Client(['verify' => false]);
        return (string) $client->get('https://catalog.bookstore.example/books')->getBody();
    }

    public function viaGuzzleChecked(): string
    {
        $client = new Client(['verify' => true]);
        return (string) $client->get('https://catalog.bookstore.example/books')->getBody();
    }

    public function viaLaravel(): array
    {
        return Http::withoutVerifying()->get('https://ledger.bookstore.example/api/balance')->json();
    }

    public function viaLaravelChecked(): array
    {
        return Http::get('https://ledger.bookstore.example/api/balance')->json();
    }

    public function viaStream()
    {
        return stream_context_create(['ssl' => ['verify_peer' => false, 'verify_peer_name' => false]]);
    }
}
