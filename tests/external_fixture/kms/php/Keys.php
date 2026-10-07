<?php

namespace App;

use Aws\Kms\KmsClient;

class Keys
{
    public function seal($data)
    {
        $kms = new KmsClient(['region' => 'eu-west-1', 'version' => 'latest']);
        return $kms->encrypt(['KeyId' => 'alias/bookstore-orders', 'Plaintext' => $data]);
    }

    public function open($blob)
    {
        $kms = new KmsClient(['region' => 'eu-west-1', 'version' => 'latest', 'credentials' => ['key' => env('AWS_ACCESS_KEY_ID'), 'secret' => env('AWS_SECRET_ACCESS_KEY')]]);
        return $kms->decrypt(['KeyId' => env('ORDERS_KMS_KEY_ID'), 'CiphertextBlob' => $blob]);
    }
}
