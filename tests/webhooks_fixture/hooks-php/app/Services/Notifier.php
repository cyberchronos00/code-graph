<?php

namespace App\Services;

use Spatie\WebhookServer\WebhookCall;

class Notifier
{
    public function invoiceSent(string $url, string $secret, array $invoice): void
    {
        WebhookCall::create()
            ->url($url)
            ->payload(['event' => 'invoice.sent', 'invoice' => $invoice])
            ->useSecret($secret)
            ->dispatch();
    }
}
