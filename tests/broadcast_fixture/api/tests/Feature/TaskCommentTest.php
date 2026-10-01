<?php

namespace Tests\Feature;

use Tests\TestCase;

class TaskCommentTest extends TestCase
{
    public function test_a_member_can_comment_on_a_task(): void
    {
        $this->postAs('/api/tasks/7/comments', ['body' => 'Looks good'])->assertCreated();
    }
}
