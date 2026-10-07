<?php

namespace App\Http\Controllers;

use Illuminate\Http\Request;

class LonelyWebhookController
{
    public function handle(Request $request, $manager)
    {
        return $manager->driver($request->input('gateway'))->handleWebhook($request);
    }
}
