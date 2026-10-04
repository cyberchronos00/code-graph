<?php

namespace App\Services;

use Illuminate\Support\Facades\Redis;
use PhpAmqpLib\Message\AMQPMessage;

class Notifier
{
    const CHANNEL = 'notifications';

    public function __construct(private $channel)
    {
    }

    public function notify(string $userId): void
    {
        Redis::publish(self::CHANNEL, json_encode(['user' => $userId]));
        $key = 'order.eu.notified';
        // $key = 'order.us.notified';
        $this->channel->basic_publish(new AMQPMessage($userId), 'shop.events', $key);
    }
}
