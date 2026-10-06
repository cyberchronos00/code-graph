<?php

namespace App\Providers;

use App\Services\PaymentsClient;
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
    }
}
