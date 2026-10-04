<?php

namespace App\Http\Controllers;

use App\Jobs\ProcessPodcast;
use App\Jobs\SendDigest;

class PodcastController extends Controller
{
    public function store()
    {
        ProcessPodcast::dispatch(1);
        SendDigest::dispatch()->onQueue('mail');   // no Horizon supervisor consumes `mail`
        return response()->json(['ok' => true]);
    }
}
