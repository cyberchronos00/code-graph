<?php

namespace App\Console\Commands;

use Illuminate\Console\Command;
use Illuminate\Support\Facades\Redis;

class ListenNotifications extends Command
{
    protected $signature = 'notifications:listen';

    public function handle(): void
    {
        Redis::subscribe(['notifications', 'cache:invalidate'], function (string $message) {
            echo $message;
        });
    }
}
