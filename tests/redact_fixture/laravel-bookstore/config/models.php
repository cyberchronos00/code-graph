<?php

return [
    'primary_key' => 'id',
    'foreign_key' => 'author_id',
    'sort_key' => 'created_at',
    'remember_token' => 'remember_token',
    'cache' => [
        'key' => 'bookstore_cache',
        'prefix' => 'bookstore',
    ],
];
