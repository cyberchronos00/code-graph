<?php

namespace App;

use RenokiCo\PhpK8s\KubernetesCluster;

class Cluster
{
    public function inCluster()
    {
        $cluster = KubernetesCluster::inClusterConfiguration();
        $cluster->pod()->create();
        return $cluster->getAllPods();
    }
}
