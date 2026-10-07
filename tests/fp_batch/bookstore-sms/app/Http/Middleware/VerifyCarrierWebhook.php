<?php

namespace App\Http\Middleware;

use Closure;
use Illuminate\Http\Request;

class VerifyCarrierWebhook
{
    public function handle(Request $request, Closure $next)
    {
        $expected = hash_hmac('sha256', $request->getContent(), (string) config('services.carrier.secret'));
        if (!hash_equals($expected, (string) $request->header('X-Carrier-Signature'))) {
            abort(401);
        }

        return $next($request);
    }
}
