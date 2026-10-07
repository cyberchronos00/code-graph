<?php

namespace App\Support;

class Gateway
{
    public static function driver(string $name)
    {
        return app("gateways.$name");
    }
}
