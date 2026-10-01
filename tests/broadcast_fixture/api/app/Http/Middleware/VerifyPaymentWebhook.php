<?php

namespace App\Http\Middleware;

class VerifyPaymentWebhook
{
    public function handle($request, $next)
    {
        $expected = hash_hmac('sha256', $request->getContent(), config('services.payments.secret'));
        abort_unless(hash_equals($expected, (string) $request->header('X-Signature')), 401);
        return $next($request);
    }
}
