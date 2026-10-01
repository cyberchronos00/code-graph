<?php

namespace Tests\Feature;

use App\Models\Task;
use PHPUnit\Framework\Attributes\Test;
use Tests\TestCase;

class TaskMoveTest extends TestCase
{
    protected function setUp(): void
    {
        parent::setUp();
        $this->signIn();
    }

    public function test_moving_a_task_updates_its_state(): void
    {
        $task = Task::create(['board_id' => 1, 'title' => 'Write docs', 'state' => 'todo']);
        $this->patchJson("/api/tasks/{$task->id}/move", ['state' => 'done'])->assertOk();
    }

    #[Test]
    public function board_tasks_are_listed(): void
    {
        $this->actingAs(new \App\Models\User())->getJson(route('tasks.index', ['board' => 1]))->assertOk();
    }

    /** @test */
    public function webhook_rejects_a_bad_signature(): void
    {
        $this->postJson('/api/webhooks/payments', [])->assertUnauthorized();
    }

    public function helperThatIsNotATest(): void
    {
    }
}
