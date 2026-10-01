<?php

namespace App\Events;

use Illuminate\Broadcasting\PrivateChannel;
use Illuminate\Contracts\Broadcasting\ShouldBroadcastNow;

/** Generic board event: the channel name is chosen by the caller. */
class BoardActivity implements ShouldBroadcastNow
{
    public function __construct(public string $channel, public string $kind, public array $payload = [])
    {
    }

    public function broadcastOn(): PrivateChannel
    {
        return new PrivateChannel($this->channel);
    }

    public function broadcastAs(): string
    {
        return 'board.activity';
    }
}
