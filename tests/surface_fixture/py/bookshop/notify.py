from postmarker.core import PostmarkClient


def send_receipt():
    pm = PostmarkClient(server_token="SURF-PY-PM-TOKEN-91c2")
    return pm.emails.send(From="shop@bookstore.example", To="a@bookstore.example", Subject="Hi", HtmlBody="Hello")
