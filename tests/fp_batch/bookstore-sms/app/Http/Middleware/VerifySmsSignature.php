<?php

namespace App\Http\Middleware;

use Closure;
use Illuminate\Http\Request;
use Symfony\Component\HttpFoundation\Response;
use Twilio\Security\RequestValidator;

class VerifySmsSignature
{
    public function handle(Request $request, Closure $next): Response
    {
        $signature = (string) $request->header('X-Twilio-Signature', '');
        $validator = new RequestValidator((string) config('services.twilio.token'));
        if (!$validator->validate($signature, $request->fullUrl(), $request->post())) {
            abort(403);
        }

        return $next($request);
    }
}
