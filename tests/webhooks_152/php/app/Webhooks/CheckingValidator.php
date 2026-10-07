<?php

namespace App\Webhooks;

class CheckingValidator
{
    public function isValid($request, $config): bool
    {
        $sig = (string) $request->header('X-Checked-Signature');
        $expect = hash_hmac('sha256', $request->getContent(), 'secret');

        return hash_equals($expect, $sig);
    }
}
