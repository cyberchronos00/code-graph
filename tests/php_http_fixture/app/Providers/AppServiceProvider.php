<?php

namespace App\Providers;

use App\Services\ArrayBoundClient;
use App\Services\AssignedBase;
use App\Services\BlankBaseClient;
use App\Services\ConfigGetClient;
use App\Services\ContextualClient;
use App\Services\DefaultBaseClient;
use App\Services\FacadeConfigClient;
use App\Services\GuzzleOrders;
use App\Services\IndexBoundClient;
use App\Services\LocalCfgClient;
use App\Services\MakeConfigClient;
use App\Services\PathClient;
use App\Services\PaymentsClient;
use Illuminate\Support\Facades\Config;
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
        $this->app->singleton(ArrayBoundClient::class, function ($app) {
            $cfg = $app['config']->get('services.payments', []);
            return new ArrayBoundClient(
                $app->make(Factory::class),
                baseUrl: (string) ($cfg['base_url'] ?? ''),
                secret: (string) ($cfg['secret'] ?? '')
            );
        });
        $this->app->singleton(IndexBoundClient::class, function ($app) {
            return new IndexBoundClient(
                $app->make(Factory::class),
                config('services.payments')['base_url']
            );
        });
        $this->app->singleton(ConfigGetClient::class, function ($app) {
            return new ConfigGetClient(
                $app->make(Factory::class),
                config()->get('services.payments.base_url')
            );
        });
        $this->app->singleton(MakeConfigClient::class, function ($app) {
            return new MakeConfigClient(
                $app->make(Factory::class),
                $app->make('config')->get('services.payments.base_url')
            );
        });
        $this->app->singleton(FacadeConfigClient::class, function ($app) {
            return new FacadeConfigClient(
                $app->make(Factory::class),
                Config::get('services.payments.base_url')
            );
        });
        $this->app->singleton(LocalCfgClient::class, function ($app) {
            $cfg = config('services.payments', []);
            return new LocalCfgClient(
                $app->make(Factory::class),
                $cfg['base_url'] ?? 'https://should-not-win.example'
            );
        });
        $this->app->singleton(BlankBaseClient::class, fn ($app) => new BlankBaseClient(
            config('services.blank.base_url')
        ));
    }
}
