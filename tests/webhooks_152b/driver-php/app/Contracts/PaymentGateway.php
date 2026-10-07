<?php

namespace App\Contracts;

use Illuminate\Http\Request;

interface PaymentGateway
{
    public function handleWebhook(Request $request);

    public function processWebhookRequest(Request $request);
}
