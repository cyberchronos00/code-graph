<?php

namespace Tests\Feature;

class PaymentsFakeTest
{
    public function test_create_payment(): void
    {
        \Illuminate\Support\Facades\Http::fake([
            'https://payments.bookstore.test/payments' => ['id' => 'p'],
            '*/payments/*/cancel' => ['ok' => true],
        ]);
    }
}
