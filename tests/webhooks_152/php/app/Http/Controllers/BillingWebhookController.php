<?php

namespace App\Http\Controllers;

class BillingWebhookController extends CashierWebhookController
{
    public function handleCustomerUpdated(array $payload)
    {
        return $payload;
    }
}
