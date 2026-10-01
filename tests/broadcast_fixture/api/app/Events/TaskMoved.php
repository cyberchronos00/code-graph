<?php

namespace App\Events;

use App\Models\Task;
use Illuminate\Broadcasting\InteractsWithSockets;
use Illuminate\Broadcasting\PrivateChannel;
use Illuminate\Contracts\Broadcasting\ShouldBroadcast;
use Illuminate\Foundation\Events\Dispatchable;

class TaskMoved implements ShouldBroadcast
{
    use Dispatchable, InteractsWithSockets;

    public function __construct(public Task $task, public int $teamId)
    {
    }

    public function broadcastOn(): array
    {
        return [
            new PrivateChannel('board.'.$this->task->board_id),
            new PrivateChannel(\App\Support\RealtimeChannels::team($this->teamId)),
        ];
    }

    public function broadcastWith(): array
    {
        return ['id' => $this->task->id, 'state' => $this->task->state];
    }
}
