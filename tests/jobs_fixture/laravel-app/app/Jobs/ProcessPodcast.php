<?php

namespace App\Jobs;

use Illuminate\Bus\Queueable;
use Illuminate\Contracts\Queue\ShouldQueue;
use Illuminate\Foundation\Bus\Dispatchable;

class ProcessPodcast implements ShouldQueue
{
    use Dispatchable, Queueable;

    public $queue = 'podcasts';

    public function __construct(public int $podcastId)
    {
    }

    public function handle(): void
    {
    }
}
