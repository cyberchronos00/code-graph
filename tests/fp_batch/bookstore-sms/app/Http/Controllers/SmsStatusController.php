<?php

namespace App\Http\Controllers;

use Illuminate\Http\Request;

class SmsStatusController
{
    public function status(Request $request)
    {
        return response()->json(['ok' => true, 'sid' => $request->input('MessageSid')]);
    }

    public function inbound(Request $request)
    {
        return response()->json(['ok' => true]);
    }

    public function carrier(Request $request)
    {
        return response()->json(['ok' => true]);
    }

    public function unchecked(Request $request)
    {
        $signature = $request->header('X-Twilio-Signature');
        logger()->info('sms callback', ['signature' => $signature]);

        return response()->json(['ok' => true]);
    }

    public function control(Request $request)
    {
        return response()->json(['ok' => true]);
    }
}
