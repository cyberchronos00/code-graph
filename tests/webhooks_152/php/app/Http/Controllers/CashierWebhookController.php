<?php

namespace App\Http\Controllers;

class CashierWebhookController extends \Laravel\Cashier\Http\Controllers\WebhookController
{
    public function handleCustomerSubscriptionCreated(array $payload)
    {
        return $this->activate($payload);
    }

    public function handleInvoicePaid(array $payload)
    {
        return $payload;
    }

    public function handleInvoicePaymentActionRequired(array $payload)
    {
        return $payload;
    }

    private function activate(array $payload)
    {
        return $payload;
    }
}
