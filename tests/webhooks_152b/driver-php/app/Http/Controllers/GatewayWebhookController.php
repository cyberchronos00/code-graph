<?php

namespace App\Http\Controllers;

use App\Support\Gateway;
use Illuminate\Http\Request;

class GatewayWebhookController
{
    public function handle(Request $request, string $gateway)
    {
        $driver = Gateway::driver($gateway);

        return $driver->handleWebhook($request);
    }
}
