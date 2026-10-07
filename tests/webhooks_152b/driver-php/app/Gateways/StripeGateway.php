<?php

namespace App\Gateways;

use App\Contracts\PaymentGateway;
use Illuminate\Http\Request;

class StripeGateway implements PaymentGateway
{
    public function handleWebhook(Request $request)
    {
        $event = \Stripe\Webhook::constructEvent($request->getContent(), $request->header('Stripe-Signature'), config('services.stripe.webhook'));
        switch ($event->type) {
            case 'invoice.paid':
                return response('paid');
            case 'charge.refunded':
                return response('refunded');
        }

        return response('ignored');
    }

    public function processWebhookRequest(Request $request)
    {
        return $this->handleWebhook($request);
    }
}
