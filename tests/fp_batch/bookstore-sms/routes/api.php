<?php

use App\Http\Controllers\SmsStatusController;
use App\Http\Middleware\VerifySmsSignature;
use Illuminate\Support\Facades\Route;

Route::post('webhooks/sms/status', [SmsStatusController::class, 'status'])
    ->middleware('verify.twilio.signature');
Route::post('webhooks/sms/inbound', [SmsStatusController::class, 'inbound'])
    ->middleware(VerifySmsSignature::class);
Route::post('webhooks/carrier/delivery', [SmsStatusController::class, 'carrier'])
    ->middleware('verify-carrier-webhook');
Route::post('webhooks/sms/unchecked', [SmsStatusController::class, 'unchecked']);
Route::post('internal/sms/resend', [SmsStatusController::class, 'control'])
    ->middleware('throttle:10,1');
Route::post('webhooks/partner/events', [SmsStatusController::class, 'control'])
    ->middleware('verify.partner.signature');
Route::post('webhooks/sms/keyed', [SmsStatusController::class, 'unchecked'])
    ->middleware('api-key');
