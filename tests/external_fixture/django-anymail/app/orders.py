from django.core.mail import EmailMessage, send_mail


def notify(user):
    send_mail("Shipped", "Your book shipped", "shop@bookstore.example", [user])


def receipt(user):
    msg = EmailMessage("Receipt", "Thanks", "shop@bookstore.example", [user])
    msg.send()
