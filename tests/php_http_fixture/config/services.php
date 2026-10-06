<?php

return [
    'payments' => [
        'base_url' => env('PAYMENTS_BASE_URL'),
        'secret' => 'supersecretvalue',
    ],
    'blank' => [
        'base_url' => env('BLANK_BASE_URL'),
    ],
    'orders' => [
        'base_url' => env('ORDERS_BASE_URL'),
    ],
    'plain' => [
        'base_url' => env('PLAIN_BASE_URL', 'https://plain.bookstore.test/api/v1'),
    ],
];
