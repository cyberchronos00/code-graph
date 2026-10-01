<?php

namespace App\Events;

use Illuminate\Broadcasting\Channel;
use Illuminate\Broadcasting\PresenceChannel;
use Illuminate\Contracts\Broadcasting\ShouldBroadcastNow;

/** Presence ping for a team lobby, plus a public echo of it (a public Channel with a declared channel's name). */
class TeamOnline implements ShouldBroadcastNow
{
    public function __construct(public int $teamId)
    {
    }

    public function broadcastOn(): array
    {
        return [
            new PresenceChannel("team.{$this->teamId}.lobby"),
            new Channel("team.{$this->teamId}"),
        ];
    }
}
