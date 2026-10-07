<?php

namespace Database\Seeders;

use Illuminate\Database\Seeder;

class HookSeeder extends Seeder
{
    public function run(): void
    {
        \DB::table('webhook_subscriptions')->insert([
            'url' => 'https://bookstore-api.test/webhooks/orders',
            'events' => ['cart.abandoned', 'wishlist.shared'],
        ]);
    }
}
