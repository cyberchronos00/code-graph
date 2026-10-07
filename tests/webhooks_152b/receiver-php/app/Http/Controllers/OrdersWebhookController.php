<?php

namespace App\Http\Controllers;

use Illuminate\Http\Request;

class OrdersWebhookController
{
    public function handle(Request $request)
    {
        $sig = $request->header('X-Bookstore-Signature');
        $expected = hash_hmac('sha256', $request->getContent(), config('services.bookstore.secret'));
        if (! hash_equals($expected, $sig)) {
            abort(401);
        }
        $payload = $request->json()->all();
        switch ($payload['event']) {
            case 'order.paid':
                $this->markPaid($payload);
                break;
        }
        return response()->noContent();
    }

    public function inventory(Request $request)
    {
        return response()->noContent();
    }

    private function markPaid(array $payload): void
    {
    }
}
