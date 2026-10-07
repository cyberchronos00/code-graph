<?php

namespace App\Jobs;

class ProcessPaymentsWebhook extends \Spatie\WebhookClient\Jobs\ProcessWebhookJob
{
    public function handle(): void
    {
        $payload = $this->webhookCall->payload ?? [];
        switch ($payload['type']) {
            case 'payment.captured':
                $this->capture();
                break;
        }
    }

    public function capture(): void
    {
    }
}
