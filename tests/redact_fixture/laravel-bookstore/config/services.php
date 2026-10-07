<?php

return [
    'ledger' => [
        'base_url' => 'http://ledger.bookstore.example/api',
        'token' => env('LEDGER_TOKEN', 'fake-bookstore-token-0000'),
        'key' => 'sk_test_FAKE_bookstore_000000',
        'idempotency_key' => 'x-idempotency-key',
    ],
    'stripe' => [
        'secret' => env('STRIPE_SECRET', 'fake-bookstore-stripe-secret-0000'),
    ],
];
