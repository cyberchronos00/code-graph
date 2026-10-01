<?php

namespace App\Http\Controllers;

use App\Events\BoardActivity;
use App\Events\TaskMoved;
use App\Events\TeamOnline;
use App\Models\Task;
use Illuminate\Http\Request;

class TaskController
{
    public function index(int $board)
    {
        broadcast(new TeamOnline(1));
        return Task::where('board_id', $board)->get();
    }

    public function move(Request $request, Task $task)
    {
        $task->update(['state' => $request->input('state')]);
        event(new TaskMoved($task, $task->board->team_id));
        broadcast(new BoardActivity("board.{$task->board_id}.activity", 'moved', ['task' => $task->id]));
        return $task;
    }

    public function comment(Request $request, Task $task)
    {
        $activity = new BoardActivity('board.'.$task->board_id.'.comments', 'comment', ['text' => $request->input('text')]);
        broadcast($activity)->toOthers();
        return response()->noContent();
    }
}
