<?php

namespace App\Broadcasting;

use App\Models\User;
use App\Support\BoardAccess;

class BoardChannel
{
    public function join(User $user, int $board): bool
    {
        return in_array($board, BoardAccess::visibleBoardIds($user), true);
    }
}
