<?php

namespace App\Http\Controllers;

use App\Mail\OrderShipped;
use Illuminate\Support\Facades\Mail;

class OrderController
{
    public function ship($user): void
    {
        Mail::to($user)->send(new OrderShipped());
    }

    public function receipt($user): void
    {
        Mail::mailer('postmark')->to($user)->queue(new OrderShipped());
    }

    public function archive($user): void
    {
        Mail::mailer('ses')->to($user)->send(new OrderShipped());
    }

    public function plain(): void
    {
        Mail::raw('Hello', fn ($m) => $m->to('a@bookstore.example'));
    }

    public function viaSmtp($user): void
    {
        Mail::mailer('smtp')->to($user)->send(new OrderShipped());
    }
}
