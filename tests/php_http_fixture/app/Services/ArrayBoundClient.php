<?php

namespace App\Services;

use Illuminate\Http\Client\Factory;
use Illuminate\Support\Facades\Config;

/** Base URL taken from a config array, never from a sibling secret. */
final class ArrayBoundClient
{
    public function __construct(private Factory $http, private string $baseUrl, private string $secret = '') {}

    public function charge(): void
    {
        $this->http->post(rtrim($this->baseUrl, '/') . '/array-bind');
    }
}

final class IndexBoundClient
{
    public function __construct(private Factory $http, private string $baseUrl) {}

    public function charge(): void
    {
        $this->http->post(rtrim($this->baseUrl, '/') . '/array-index');
    }
}

final class ConfigGetClient
{
    public function __construct(private Factory $http, private string $baseUrl) {}

    public function charge(): void
    {
        $this->http->post(rtrim($this->baseUrl, '/') . '/config-get');
    }
}

final class MakeConfigClient
{
    public function __construct(private Factory $http, private string $baseUrl) {}

    public function charge(): void
    {
        $this->http->post(rtrim($this->baseUrl, '/') . '/make-config');
    }
}

final class FacadeConfigClient
{
    public function __construct(private Factory $http, private string $baseUrl) {}

    public function charge(): void
    {
        $this->http->post(rtrim($this->baseUrl, '/') . '/facade-config');
    }
}

final class LocalCfgClient
{
    public function __construct(private Factory $http, private string $baseUrl) {}

    public function charge(): void
    {
        $this->http->post(rtrim($this->baseUrl, '/') . '/local-cfg');
    }
}

final class BlankBaseClient
{
    public function __construct(private string $baseUrl) {}

    public function ping(): void
    {
        \Illuminate\Support\Facades\Http::get(rtrim($this->baseUrl, '/') . '/blank');
    }
}

final class RelativeClient
{
    public function ping(): void
    {
        \Illuminate\Support\Facades\Http::get('/relative-only');
    }
}

final class EncodedPathClient
{
    public function status(string $paymentId, OrderRef $order): void
    {
        \Illuminate\Support\Facades\Http::get('/payments/' . rawurlencode($paymentId) . '/status');
        \Illuminate\Support\Facades\Http::get('/orders/' . strval($order->id) . '/view');
        \Illuminate\Support\Facades\Http::get('/refunds/' . urlencode($paymentId) . '/start');
        \Illuminate\Support\Facades\Http::get('/trim/' . trim($paymentId) . '/end');
        \Illuminate\Support\Facades\Http::get('/cast/' . (string) $paymentId . '/end');
        \Illuminate\Support\Facades\Http::get('/str/' . \Illuminate\Support\Str::of($paymentId)->toString() . '/end');
        \Illuminate\Support\Facades\Http::get(sprintf('/pay/%s/x', rawurlencode($order->id)));
    }
}

final class OrderRef
{
    public string $id = '';
}
