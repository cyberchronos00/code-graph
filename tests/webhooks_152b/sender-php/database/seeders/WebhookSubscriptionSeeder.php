<?php

namespace Database\Seeders;

use App\Models\WebhookSubscription;
use Illuminate\Database\Seeder;

class WebhookSubscriptionSeeder extends Seeder
{
    public function run(): void
    {
        WebhookSubscription::create([
            'url' => 'https://bookstore-api.test/webhooks/orders',
            'event' => 'order.paid',
            'secret' => 'seed-secret',
        ]);
        WebhookSubscription::create([
            'url' => 'https://elsewhere.test/hooks/none',
            'event' => 'order.shipped',
            'secret' => 'seed-secret',
        ]);
    }
}
