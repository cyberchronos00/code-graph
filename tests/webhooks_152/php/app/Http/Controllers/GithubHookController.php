<?php

namespace App\Http\Controllers;

#[\Spatie\RouteAttributes\Attributes\Prefix('api')]
#[\Spatie\RouteAttributes\Attributes\Middleware('api')]
class GithubHookController
{
    #[\Spatie\RouteAttributes\Attributes\Post('github/webhooks', name: 'github.webhooks', middleware: 'verify.github')]
    public function handle(\Illuminate\Http\Request $request)
    {
        $this->authorize('deploy');
        $event = $request->header('X-GitHub-Event');
        if ($event === 'push') {
            $this->deploy();
        }

        return response('ok');
    }

    #[\Spatie\RouteAttributes\Attributes\Prefix('hooks')]
    #[\Spatie\RouteAttributes\Attributes\Middleware('auth')]
    #[\Spatie\RouteAttributes\Attributes\Post('saleor')]
    public function saleor()
    {
        $this->authorize('saleor');

        return response('ok');
    }

    #[\Spatie\RouteAttributes\Attributes\Get('github/webhooks')]
    public function show()
    {
        return response('ok');
    }

    public function deploy()
    {
        return 'ok';
    }
}
