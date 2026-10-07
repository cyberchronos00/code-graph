<?php

namespace App\Notifications;

class OrderPacked
{
    public function via($notifiable): array
    {
        return ['mail', 'vonage'];
    }
}
