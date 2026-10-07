<?php

namespace App\Notifications;

use Illuminate\Notifications\Notification;
use NotificationChannels\Apn\ApnChannel;
use NotificationChannels\Fcm\FcmChannel;

class OrderShipped extends Notification
{
    public function via($notifiable)
    {
        return [FcmChannel::class];
    }
}

class OrderShippedIos extends Notification
{
    public function via($notifiable)
    {
        return [ApnChannel::class];
    }
}
