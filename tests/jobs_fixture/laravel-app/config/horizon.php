<?php

return [
    'defaults' => [
        'supervisor-1' => [
            'connection' => 'redis',
            'queue' => ['podcasts', 'default'],
            'maxProcesses' => 4,
        ],
    ],
];
