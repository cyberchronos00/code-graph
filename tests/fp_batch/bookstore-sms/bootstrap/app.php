<?php

use App\Http\Middleware\VerifySmsSignature;
use Illuminate\Foundation\Application;
use Illuminate\Foundation\Configuration\Middleware;

return Application::configure(basePath: dirname(__DIR__))
    ->withMiddleware(function (Middleware $middleware) {
        $middleware->alias([
            'verify.twilio.signature' => VerifySmsSignature::class,
            'verify-carrier-webhook' => \App\Http\Middleware\VerifyCarrierWebhook::class,
        ]);
    })
    ->create();
