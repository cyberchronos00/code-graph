<?php

namespace App;

use Minishlink\WebPush\WebPush;
use Pushok\AuthProvider\Token;
use Pushok\Client;
use Kreait\Firebase\Factory;

class Pushers
{
    public function ios($deviceToken)
    {
        $auth = Token::create([
            'key_id' => env('APNS_KEY_ID'),
            'team_id' => env('APNS_TEAM_ID'),
            'app_bundle_id' => 'com.bookstore.app',
            'private_key_path' => env('APNS_KEY_PATH'),
            'private_key_secret' => null,
        ]);
        $client = new Client($auth, true);
        $client->push();
    }

    public function browser($subscription)
    {
        $webPush = new WebPush(['VAPID' => [
            'subject' => 'mailto:ops@bookstore.example',
            'publicKey' => env('VAPID_PUBLIC_KEY'),
            'privateKey' => env('VAPID_PRIVATE_KEY'),
        ]]);
        $webPush->sendOneNotification($subscription, '{"title":"Back in stock"}');
    }

    public function android($token)
    {
        $messaging = (new Factory)->withServiceAccount(env('FIREBASE_CREDENTIALS'))->createMessaging();
        $messaging->send(['token' => $token]);
    }
}
