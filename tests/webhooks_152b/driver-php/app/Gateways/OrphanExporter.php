<?php

namespace App\Gateways;

class OrphanExporter
{
    public function handleWebhook($request)
    {
        return hash_hmac('sha256', 'x', 'y');
    }
}
