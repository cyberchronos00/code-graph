import os

import messagebird
import plivo


def via_messagebird(to):
    client = messagebird.Client(os.environ["MESSAGEBIRD_API_KEY"])
    client.message_create("Bookstore", [to], "Shipped")


def via_plivo(to):
    client = plivo.RestClient("MAXXXXXXXXXXXXXXXXXX", "literal-auth-token-0123456789")
    client.messages.create(src="+15550001", dst=to, text="Shipped")
