<?php

namespace App\Services;

class Ingest
{
    public function serve(): void
    {
        $server = stream_socket_server('tcp://0.0.0.0:7300', $errno, $errstr);
        while ($conn = stream_socket_accept($server)) {
            fwrite($conn, fgets($conn));
        }
    }

    public function serveLocal(): void
    {
        $server = stream_socket_server('tcp://127.0.0.1:7301', $errno, $errstr);
        while ($conn = stream_socket_accept($server)) {
            fclose($conn);
        }
    }
}
