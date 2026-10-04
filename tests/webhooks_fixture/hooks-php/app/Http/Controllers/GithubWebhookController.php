<?php

namespace App\Http\Controllers;

use Illuminate\Http\Request;

class GithubWebhookController extends Controller
{
    public function handle(Request $request)
    {
        if (! $this->validSignature($request)) {
            abort(401);
        }
        $event = $request->header('X-GitHub-Event');
        if ($event === 'push') {
            return $this->deploy($request->all());
        }

        return response('ignored');
    }

    public function gitlab(Request $request)
    {
        $event = $request->header('X-Gitlab-Event');

        return match ($event) {
            'Merge Request Hook', 'Push Hook' => $this->deploy($request->all()),
            default => response('ignored'),
        };
    }

    private function validSignature(Request $request): bool
    {
        $expected = 'sha256='.hash_hmac('sha256', $request->getContent(), config('services.github.secret'));

        return hash_equals($expected, (string) $request->header('X-Hub-Signature-256'));
    }

    private function deploy(array $payload)
    {
        return $payload;
    }
}
