<?php

return [
    'default' => env('MAIL_MAILER', 'smtp'),

    'mailers' => [
        'smtp' => ['transport' => 'smtp', 'host' => env('MAIL_HOST', 'localhost')],
        'mailgun' => ['transport' => 'mailgun'],
        'postmark' => ['transport' => 'postmark'],
        'ses' => ['transport' => 'ses'],
        'sendmail' => ['transport' => 'sendmail', 'path' => '/usr/sbin/sendmail'],
    ],
];
