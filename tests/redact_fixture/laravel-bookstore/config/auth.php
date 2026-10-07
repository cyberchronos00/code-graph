<?php

return [
    'defaults' => [
        'guard' => env('AUTH_GUARD', 'web'),
        'passwords' => env('AUTH_PASSWORD_BROKER', 'readers'),
    ],
    'passwords' => [
        'readers' => [
            'provider' => 'readers',
            'table' => env('AUTH_PASSWORD_RESET_TOKEN_TABLE', 'reader_password_resets'),
            'expire' => 60,
        ],
    ],
];
