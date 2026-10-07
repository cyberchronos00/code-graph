<?php

namespace App;

use Docker\Docker;

class Runner
{
    public function run()
    {
        $docker = Docker::create();
        return $docker->containerList();
    }
}
