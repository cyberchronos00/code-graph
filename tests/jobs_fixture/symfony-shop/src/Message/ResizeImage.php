<?php

namespace App\Message;

final class ResizeImage
{
    public function __construct(public readonly string $path)
    {
    }
}
