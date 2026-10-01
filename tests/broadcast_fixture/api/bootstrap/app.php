<?php

use App\Http\Middleware\VerifyPaymentWebhook;
use Illuminate\Foundation\Application;
use Illuminate\Foundation\Configuration\Middleware;

return Application::configure(basePath: dirname(__DIR__))
    ->withRouting(api: __DIR__.'/../routes/api.php')
    ->withBroadcasting(__DIR__.'/../routes/channels.php', ['prefix' => 'api', 'middleware' => ['api', 'auth:sanctum']])
    ->withMiddleware(function (Middleware $middleware) {
        $middleware->alias(['verify.payment.webhook' => VerifyPaymentWebhook::class]);
    })
    ->create();
