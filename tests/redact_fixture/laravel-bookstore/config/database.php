<?php

return [
    'default' => 'pgsql',
    'connections' => [
        'pgsql' => [
            'driver' => 'pgsql',
            'host' => 'pg.bookstore.example',
            'password' => env('DB_PASSWORD', 'fake-bookstore-pw-0000'),
        ],
    ],
];
