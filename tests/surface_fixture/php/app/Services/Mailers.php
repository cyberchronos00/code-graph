<?php

namespace App\Services;

class Mailers
{
    public function receipt(string $body): void
    {
        $mg = \Mailgun\Mailgun::create('SURF-PHP-MAILGUN-aa17');
        $mg->messages()->send('mg.bookstore.example', ['from' => 'shop@bookstore.example', 'text' => $body]);
    }
}
