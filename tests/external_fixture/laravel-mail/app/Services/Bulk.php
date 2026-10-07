<?php

namespace App\Services;

class Bulk
{
    public function blast(): void
    {
        $sg = new \SendGrid(config('services.sendgrid.key'));
        $sg->send(new \SendGrid\Mail\Mail());
    }
}
