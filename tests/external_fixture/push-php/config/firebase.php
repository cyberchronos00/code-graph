<?php

return [
    'default' => 'app',
    'projects' => [
        'app' => [
            'credentials' => ['file' => env('FIREBASE_CREDENTIALS', env('GOOGLE_APPLICATION_CREDENTIALS'))],
        ],
    ],
];
