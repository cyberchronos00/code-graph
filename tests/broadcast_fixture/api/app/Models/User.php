<?php

namespace App\Models;

use Illuminate\Foundation\Auth\User as Authenticatable;

class User extends Authenticatable
{
    public function teams()
    {
        return $this->belongsToMany(Team::class);
    }

    public function belongsToTeam(int $teamId): bool
    {
        return $this->teams()->where('teams.id', $teamId)->exists();
    }

    public function isBoardMember(int $boardId): bool
    {
        return Board::where('id', $boardId)->whereIn('team_id', $this->teams()->pluck('teams.id'))->exists();
    }
}
