<?php

namespace App\Message;

final class SendInvoice
{
    public function __construct(public readonly int $orderId)
    {
    }
}
