<?php

namespace App\Http\Controllers;

use App\Services\FeedPuller;
use GuzzleHttp\Client;
use Illuminate\Http\Request;
use Illuminate\Support\Facades\Http;

class FetchController
{
    private const ALLOWED_HOSTS = ['hooks.bookstore.test'];

    public function cover(Request $request)
    {
        $target = $request->query('url');
        return Http::get($target)->body();
    }

    public function byIsbn(Request $request, string $isbn)
    {
        return Http::get("https://covers.bookstore.test/images/{$isbn}.jpg")->body();
    }

    public function notify(Request $request)
    {
        $callback = $request->input('callback');
        $host = parse_url($callback, PHP_URL_HOST);
        if (!in_array($host, self::ALLOWED_HOSTS, true)) {
            abort(400);
        }
        return Http::post($callback, ['ok' => true])->status();
    }

    public function import(Request $request, FeedPuller $puller)
    {
        return $puller->pull($request->input('feed'));
    }

    public function mirror(Request $request)
    {
        return gethostbyname($request->query('host'));
    }

    public function lookup(Request $request)
    {
        $client = new Client();
        return $client->get('https://isbn.bookstore.test/lookup?code=' . $request->query('code'))->getBody();
    }

    public function raw(Request $request)
    {
        $link = $_POST['link'];
        return file_get_contents($link);
    }
}
