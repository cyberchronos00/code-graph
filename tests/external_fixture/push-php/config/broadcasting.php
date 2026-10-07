<?php

return [
    'connections' => [
        'apn' => [
            'key_id' => env('APN_KEY_ID'),
            'team_id' => env('APN_TEAM_ID'),
            'app_bundle_id' => 'com.bookstore.app',
            'private_key_path' => env('APN_PRIVATE_KEY_PATH'),
        ],
    ],
];
