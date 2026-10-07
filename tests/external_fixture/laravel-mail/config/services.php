<?php

return [
    'mailgun' => [
        'domain' => env('MAILGUN_DOMAIN'),
        'secret' => env('MAILGUN_SECRET'),
        'endpoint' => env('MAILGUN_ENDPOINT', 'api.mailgun.net'),
    ],
    'postmark' => [
        'token' => env('POSTMARK_TOKEN'),
    ],
    'sendgrid' => [
        'key' => env('SENDGRID_API_KEY'),
    ],
    'vonage' => [
        'key' => env('VONAGE_KEY'),
        'secret' => env('VONAGE_SECRET'),
    ],
];
