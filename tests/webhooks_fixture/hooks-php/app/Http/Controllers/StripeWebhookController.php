<?php

namespace App\Http\Controllers;

use Illuminate\Http\Request;
use Stripe\Webhook;

class StripeWebhookController extends Controller
{
    public function handle(Request $request)
    {
        $event = Webhook::constructEvent($request->getContent(), $request->header('Stripe-Signature'), config('services.stripe.webhook'));
        switch ($event->type) {
            case 'invoice.payment_failed':
                $this->paymentFailed($event);
                break;
            case 'customer.subscription.updated':
                $this->subscriptionUpdated($event);
                break;
        }

        return response('ok');
    }

    public function open(Request $request)
    {
        $signature = $request->header('Stripe-Signature');
        $payload = $request->all();
        if ($payload['type'] === 'payout.paid') {
            $this->paymentFailed($payload);
        }

        return response('ok');
    }

    private function paymentFailed($event)
    {
        return $event;
    }

    private function subscriptionUpdated($event)
    {
        return $event;
    }
}
