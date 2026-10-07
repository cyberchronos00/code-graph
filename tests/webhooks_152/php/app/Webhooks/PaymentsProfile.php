<?php

namespace App\Webhooks;

class PaymentsProfile implements \Spatie\WebhookClient\WebhookProfile\WebhookProfile
{
    public function shouldProcess($request): bool
    {
        $event = $request->all();
        if ($event['type'] === 'payment.failed') {
            return false;
        }

        return true;
    }
}
