<?php

namespace App\Http\Controllers;

use App\Models\GatewayRow;
use Illuminate\Http\Request;

class ManagedWebhookController
{
    private array $gateways = [];

    public function handle(Request $request)
    {
        $row = GatewayRow::first();

        return $this->gateways[$row->gateway]->processWebhookRequest($request);
    }
}
