<?php

return [
    'github' => [
        'secret' => 'SURF-PHP-GH-SECRET-1d4e',
    ],
    'ledger' => [
        'base_url' => 'http://ledger.bookstore.example/api',
        'token' => env('LEDGER_TOKEN', 'SURF-PHP-LEDGER-DEFAULT-d7e2'),
    ],
];
