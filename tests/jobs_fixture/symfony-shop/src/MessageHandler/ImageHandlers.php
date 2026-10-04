<?php

namespace App\MessageHandler;

use App\Message\ResizeImage;
use Symfony\Component\Messenger\Attribute\AsMessageHandler;

final class ImageHandlers
{
    #[AsMessageHandler(fromTransport: 'images')]
    public function resize(ResizeImage $message): void
    {
    }
}
