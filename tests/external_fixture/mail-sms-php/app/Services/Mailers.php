<?php

namespace App\Services;

class Mailers
{
    public function viaSendgrid(string $to): void
    {
        $sg = new \SendGrid(getenv('SENDGRID_API_KEY'));
        $mail = new \SendGrid\Mail\Mail();
        $sg->send($mail);
    }

    public function viaMailgun(string $body): void
    {
        $mg = \Mailgun\Mailgun::create('key-0123456789abcdef');
        $mg->messages()->send('mg.bookstore.example', ['from' => 'shop@bookstore.example', 'text' => $body]);
    }

    public function viaPostmark(): void
    {
        $client = new \Postmark\PostmarkClient(env('POSTMARK_TOKEN'));
        $client->sendEmail('shop@bookstore.example', 'a@bookstore.example', 'Hi', 'Hello');
    }

    public function viaResend(): void
    {
        $resend = \Resend::client($_ENV['RESEND_API_KEY']);
        $resend->emails->send(['from' => 'shop@bookstore.example', 'to' => 'a@bookstore.example']);
    }

    public function sms(string $to): void
    {
        $twilio = new \Twilio\Rest\Client(getenv('TWILIO_SID'), getenv('TWILIO_TOKEN'));
        $twilio->messages->create($to, ['from' => '+15550001', 'body' => 'Shipped']);
    }

    public function vonageSms(string $to): void
    {
        $client = new \Vonage\Client(new \Vonage\Client\Credentials\Basic(getenv('VONAGE_KEY'), getenv('VONAGE_SECRET')));
        $client->sms()->send(new \Vonage\SMS\Message\SMS($to, 'Shop', 'Shipped'));
    }

    public function noKey(\SendGrid $injected): void
    {
        $mailer = new \SendGrid($this->key);
        $mailer->send(new \SendGrid\Mail\Mail());
    }
}
