<?php

namespace App\Http\Controllers;

use App\Events\StatusPage;
use Illuminate\Http\Request;

class WebhookController
{
    public function payment(Request $request)
    {
        event(new StatusPage());
        return response()->noContent();
    }

    public function download(Request $request, string $file)
    {
        return response()->download(storage_path("exports/{$file}"));
    }
}
