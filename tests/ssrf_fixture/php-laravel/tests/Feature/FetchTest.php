<?php

namespace Tests\Feature;

use Illuminate\Http\Request;
use Illuminate\Support\Facades\Http;

class FetchTest
{
    public function testCover(Request $request)
    {
        $target = $request->query('url');
        return Http::get($target)->body();
    }
}
