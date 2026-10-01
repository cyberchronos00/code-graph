<?php

namespace App\Support;

use App\Models\User;

class BoardAccess
{
    /** Board ids the user may see (admins see every board). */
    public static function visibleBoardIds(User $user): array
    {
        if ($user->is_admin) {
            return \App\Models\Board::pluck('id')->all();
        }
        return \App\Models\Board::whereIn('team_id', $user->teams()->pluck('teams.id'))->pluck('id')->all();
    }
}
