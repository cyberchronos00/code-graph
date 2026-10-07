<?php

namespace App\Enums;

enum WebhookEvent: string
{
    case OrderPaid = 'order.paid';
    case OrderShipped = 'order.shipped';
    case BookRestocked = 'book.restocked';
}
