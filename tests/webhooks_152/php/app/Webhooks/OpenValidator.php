<?php

namespace App\Webhooks;

class OpenValidator
{
    public function isValid($request, $config): bool
    {
        return true;
    }
}
