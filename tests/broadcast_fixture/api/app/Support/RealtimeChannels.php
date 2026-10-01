<?php

namespace App\Support;

class RealtimeChannels
{
    public static function team(int $teamId): string
    {
        return "team.{$teamId}";
    }

    public static function boardTopic(int $boardId, string $topic): string
    {
        return sprintf('board.%d.%s', $boardId, $topic);
    }
}
