import os

import resend
import vonage
from postmarker.core import PostmarkClient
from sendgrid import SendGridAPIClient
from twilio.rest import Client

resend.api_key = os.environ["RESEND_API_KEY"]


def via_sendgrid(message):
    sg = SendGridAPIClient(os.environ["SENDGRID_API_KEY"])
    return sg.send(message)


def via_postmark():
    pm = PostmarkClient(server_token="server-token-literal-0123")
    return pm.emails.send(From="shop@bookstore.example", To="a@bookstore.example", Subject="Hi", HtmlBody="Hello")


def via_resend():
    return resend.Emails.send({"from": "shop@bookstore.example", "to": "a@bookstore.example", "subject": "Hi", "html": "Hello"})


def sms(to):
    client = Client(os.environ["TWILIO_ACCOUNT_SID"], os.environ["TWILIO_AUTH_TOKEN"])
    return client.messages.create(to=to, from_="+15550001", body="Shipped")


def vonage_sms(to):
    client = vonage.Client(key=os.getenv("VONAGE_API_KEY"), secret=os.getenv("VONAGE_API_SECRET"))
    return vonage.Sms(client).send_message({"from": "Shop", "to": to, "text": "Shipped"})


def no_key(sg_client):
    other = SendGridAPIClient(get_key())
    return other.send(None)
