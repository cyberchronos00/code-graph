<?php

namespace App\MessageHandler;

use App\Message\SendInvoice;
use Symfony\Component\Messenger\Attribute\AsMessageHandler;

#[AsMessageHandler]
final class SendInvoiceHandler
{
    public function __invoke(SendInvoice $message): void
    {
    }
}
