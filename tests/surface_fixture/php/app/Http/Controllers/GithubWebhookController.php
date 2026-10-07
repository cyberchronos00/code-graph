<?php

namespace App\Http\Controllers;

use Illuminate\Http\Request;

class GithubWebhookController extends Controller
{
    public function handle(Request $request)
    {
        $expected = 'sha256=' . hash_hmac('sha256', $request->getContent(), config('services.github.secret'));
        if (!hash_equals($expected, (string) $request->header('X-Hub-Signature-256'))) {
            abort(401);
        }

        return response('ok');
    }

    public function open(Request $request)
    {
        $event = $request->header('X-GitHub-Event');
        $payload = $request->all();
        if ($event === 'issues') {
            return response($payload['action'] ?? 'none');
        }

        return response('ok');
    }
}
