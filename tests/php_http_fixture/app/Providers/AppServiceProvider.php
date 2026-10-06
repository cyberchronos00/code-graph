<?php

namespace App\Providers;

use App\Services\AssignedBase;
use App\Services\ContextualClient;
use App\Services\DefaultBaseClient;
use App\Services\GuzzleOrders;
use App\Services\PathClient;
use App\Services\PaymentsClient;
use GuzzleHttp\Client;
use Illuminate\Http\Client\Factory;
use Illuminate\Support\ServiceProvider;

class AppServiceProvider extends ServiceProvider
{
    public function register(): void
    {
        $this->app->singleton(PaymentsClient::class, fn ($app) => new PaymentsClient(
            $app->make(Factory::class),
            config('services.payments.base_url')
        ));
        $this->app->singleton(GuzzleOrders::class, fn ($app) => new GuzzleOrders(
            new Client(['base_uri' => config('services.orders.base_url')])
        ));
        $this->app->when(ContextualClient::class)->needs('$baseUrl')->give(config('services.orders.base_url'));
        $this->app->bind(PathClient::class, function ($app) {
            return new PathClient($app->make(Factory::class), config('services.orders.base_url'));
        });
        $this->app->singleton(AssignedBase::class, fn ($app) => new AssignedBase(
            $app->make(Factory::class),
            config('services.payments.base_url')
        ));
        $this->app->singleton(DefaultBaseClient::class, fn ($app) => new DefaultBaseClient(
            config('services.plain.base_url')
        ));
    }
}
