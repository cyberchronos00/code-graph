<?php

namespace App\Controller;

use App\Message\AuditLog;
use App\Message\ResizeImage;
use App\Message\SendInvoice;
use Symfony\Component\HttpFoundation\JsonResponse;
use Symfony\Component\Messenger\MessageBusInterface;
use Symfony\Component\Routing\Attribute\Route;

final class OrderController
{
    public function __construct(private MessageBusInterface $bus)
    {
    }

    #[Route('/orders', methods: ['POST'])]
    public function create(): JsonResponse
    {
        $this->bus->dispatch(new SendInvoice(1));
        $this->bus->dispatch(new ResizeImage('a.png'));
        $this->bus->dispatch(new AuditLog('created'));   // routed to `audit`: no worker consumes it
        return new JsonResponse(['ok' => true]);
    }
}
