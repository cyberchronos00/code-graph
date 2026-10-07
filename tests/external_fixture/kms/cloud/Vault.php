<?php

namespace App;

use Google\Cloud\SecretManager\V1\SecretManagerServiceClient;

class Vault
{
    public function dbPassword()
    {
        $client = new SecretManagerServiceClient(['credentials' => env('GOOGLE_APPLICATION_CREDENTIALS')]);
        return $client->accessSecretVersion('projects/bookstore-prod/secrets/db-password/versions/latest');
    }

    public function ambient()
    {
        $client = new SecretManagerServiceClient();
        return $client->accessSecretVersion('projects/bookstore-prod/secrets/mail-token/versions/latest');
    }
}
