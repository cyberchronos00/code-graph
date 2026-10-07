<?php

namespace App\Events;

use Illuminate\Broadcasting\InteractsWithSockets;
use Illuminate\Broadcasting\PrivateChannel;
use Illuminate\Contracts\Broadcasting\ShouldBroadcast;
use Illuminate\Foundation\Events\Dispatchable;

class OrderShipped implements ShouldBroadcast
{
    use Dispatchable, InteractsWithSockets;

    public function __construct(public int $storeId, public int $orderId)
    {
    }

    public function broadcastOn(): array
    {
        return [new PrivateChannel('store.'.$this->storeId.'.orders')];
    }

    public function broadcastAs(): string
    {
        return 'OrderShipped';
    }
}
