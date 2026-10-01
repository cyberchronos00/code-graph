<?php

use App\Broadcasting\BoardChannel;
use App\Models\User;
use Illuminate\Support\Facades\Broadcast;

Broadcast::channel('App.Models.User.{id}', function ($user, $id) {
    return (int) $user->id === (int) $id;
});

Broadcast::channel('team.{teamId}', function ($user, $teamId) {
    return $user->belongsToTeam((int) $teamId);
});

Broadcast::channel('team.{teamId}.lobby', function (User $user, $teamId) {
    if ($user->belongsToTeam((int) $teamId)) {
        return ['id' => $user->id, 'name' => $user->name];
    }
    return false;
});

Broadcast::channel('board.{board}', BoardChannel::class);

// every topic of a board is open to any board member
Broadcast::channel('board.{boardId}.{topic}', function ($user, $boardId) {
    return $user->isBoardMember((int) $boardId);
});
