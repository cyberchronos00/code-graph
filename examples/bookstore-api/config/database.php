<?php

return [
    'default' => env('DB_CONNECTION', 'mysql'),
    'connections' => [
        'mysql' => [
            'driver' => 'mysql',
            'host' => env('DB_HOST', '127.0.0.1'),
            'database' => env('DB_DATABASE', 'bookstore'),
        ],
        'warehouse' => [
            'driver' => 'mysql',
            'host' => env('WAREHOUSE_DB_HOST', '127.0.0.1'),
            'database' => env('WAREHOUSE_DB_DATABASE', 'warehouse'),
        ],
    ],
];
