<?php

namespace Tests;

class FetcherTest
{
    public function testCurl(): void
    {
        $ch = curl_init('https://prices.bookstore.example/today');
        curl_setopt($ch, CURLOPT_SSL_VERIFYPEER, false);
    }
}
