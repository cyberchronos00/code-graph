<?php

namespace App\Events;

use Illuminate\Contracts\Broadcasting\ShouldBroadcast;

class StatusPage implements ShouldBroadcast
{
    public function broadcastOn(): string
    {
        return 'status';
    }
}
