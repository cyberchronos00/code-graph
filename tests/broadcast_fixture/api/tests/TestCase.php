<?php

namespace Tests;

use Illuminate\Foundation\Testing\TestCase as BaseTestCase;

abstract class TestCase extends BaseTestCase
{
    protected function signIn(): \App\Models\User
    {
        $user = new \App\Models\User();
        $this->actingAs($user);
        return $user;
    }

    // request helpers: the verb and URL arrive as parameters
    protected function sendAs(string $method, string $uri, array $data = []): \Illuminate\Testing\TestResponse
    {
        $this->signIn();
        return $this->json($method, $uri, $data);
    }

    protected function postAs(string $uri, array $data = []): \Illuminate\Testing\TestResponse
    {
        return $this->sendAs('post', $uri, $data);
    }
}
