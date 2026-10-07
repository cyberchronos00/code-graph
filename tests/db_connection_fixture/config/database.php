<?php

return [
    'default' => env('DB_CONNECTION', 'mysql'),
    'connections' => [
        'mysql' => [
            'driver' => 'mysql',
            'host' => 'db.internal.example',
            'database' => 'bookstore',
        ],
        'warehouse' => [
            'driver' => 'mysql',
            'host' => 'warehouse.internal.example',
            'database' => 'warehouse',
        ],
        'reporting' => [
            'driver' => 'mysql',
            'host' => 'reports.internal.example',
            'database' => 'reports',
        ],
    ],
];
