<?php

namespace App\Models;

use App\Enums\WebhookEvent;
use Illuminate\Database\Eloquent\Model;

class WebhookSubscription extends Model
{
    protected $casts = [
        'event' => WebhookEvent::class,
    ];
}
