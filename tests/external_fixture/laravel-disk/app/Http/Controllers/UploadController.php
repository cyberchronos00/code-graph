<?php

namespace App\Http\Controllers;

use Illuminate\Support\Facades\Storage;

class UploadController
{
    public function store(string $body): void
    {
        Storage::disk('s3')->put('a.txt', $body);
    }

    public function direct(string $body): void
    {
        $s3 = new \Aws\S3\S3Client(['version' => 'latest', 'region' => 'us-east-1']);
        $s3->putObject(['Bucket' => 'media', 'Key' => 'a.txt', 'Body' => $body]);
    }
}
