<?php

class MailersTest
{
    public function testSend(): void
    {
        $sg = new \SendGrid('SG.test-literal-key');
        $sg->send(new \SendGrid\Mail\Mail());
    }
}
