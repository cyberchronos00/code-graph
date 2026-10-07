<?php

namespace App;

class Sms
{
    public function phpMessagebird($to)
    {
        $client = new \MessageBird\Client(env('MESSAGEBIRD_API_KEY'));
        $client->messages->create(new \MessageBird\Objects\Message());
    }

    public function phpPlivo($to)
    {
        $client = new \Plivo\RestClient(env('PLIVO_AUTH_ID'), env('PLIVO_AUTH_TOKEN'));
        $client->messages->create('+15550001', [$to], 'Shipped');
    }
}
