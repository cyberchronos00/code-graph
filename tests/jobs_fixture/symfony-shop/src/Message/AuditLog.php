<?php

namespace App\Message;

final class AuditLog
{
    public function __construct(public readonly string $line)
    {
    }
}
