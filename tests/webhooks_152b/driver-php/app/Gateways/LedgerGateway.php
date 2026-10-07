<?php

namespace App\Gateways;

use App\Contracts\PaymentGateway;
use Illuminate\Http\Request;

class LedgerGateway implements PaymentGateway
{
    public function handleWebhook(Request $request)
    {
        $expected = hash_hmac('sha256', $request->getContent(), config('services.ledger.secret'));
        if (! hash_equals($expected, (string) $request->header('X-Ledger-Signature'))) {
            abort(401);
        }

        return response('ok');
    }

    public function processWebhookRequest(Request $request)
    {
        return $this->handleWebhook($request);
    }
}
