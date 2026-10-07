<?php

namespace App\Http\Concerns;

trait ManagesOrders
{
    protected function assertCanManage($order): void
    {
        abort(403);
    }

    protected function assertOwns($order): void
    {
        abort(403);
    }
}
