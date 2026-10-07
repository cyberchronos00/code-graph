<?php

namespace App\Http\Controllers;

class LooseHookController
{
    public function handle(\Illuminate\Http\Request $request)
    {
        $sig = $request->header(SALEOR_SIGNATURE_HEADER);
        $expect = hash_hmac('sha256', $request->getContent(), 'secret');
        if (!hash_equals($expect, (string) $sig)) {
            abort(400);
        }

        return response('ok');
    }
}
