<?php

return [
    'configs' => [
        [
            'name' => 'payments',
            'signature_header_name' => 'X-Payments-Signature',
            'signing_secret' => env('PAYMENTS_WEBHOOK_SECRET'),
            'process_webhook_job' => \App\Jobs\ProcessPaymentsWebhook::class,
            'webhook_profile' => \App\Webhooks\PaymentsProfile::class,
        ],
        [
            'name' => 'checked',
            'signature_header_name' => 'X-Checked-Signature',
            'signing_secret' => env('CHECKED_WEBHOOK_SECRET'),
            'signature_validator' => \App\Webhooks\CheckingValidator::class,
            'process_webhook_job' => \App\Jobs\ProcessPaymentsWebhook::class,
        ],
        [
            'name' => 'open',
            'signature_validator' => \App\Webhooks\OpenValidator::class,
            'process_webhook_job' => \App\Jobs\ProcessPaymentsWebhook::class,
        ],
        [
            'name' => 'blank',
            'process_webhook_job' => \App\Jobs\ProcessPaymentsWebhook::class,
        ],
    ],
];
