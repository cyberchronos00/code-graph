import os

from aioapns import APNs, NotificationRequest
from apns2.client import APNsClient
from apns2.credentials import TokenCredentials
from firebase_admin import messaging
from pywebpush import webpush


def notify_shipped(token):
    messaging.send(messaging.Message(token=token, notification=messaging.Notification(title="Shipped")))


def notify_ios(device_token):
    creds = TokenCredentials(auth_key_path=os.environ["APNS_KEY_PATH"], auth_key_id=os.environ["APNS_KEY_ID"], team_id=os.environ["APNS_TEAM_ID"])
    client = APNsClient(creds, use_sandbox=False)
    client.send_notification(device_token, {"aps": {"alert": "Shipped"}}, "com.bookstore.app")


async def notify_ios_async(device_token):
    apns = APNs(key="certs/AuthKey_BOOKSTORE.p8", key_id="ABC123DEFG", team_id="TEAM123456", topic="com.bookstore.app")
    await apns.send_notification(NotificationRequest(device_token=device_token, message={}))


def notify_browser(subscription):
    webpush(subscription_info=subscription, data="Back in stock", vapid_private_key=os.environ["VAPID_PRIVATE_KEY"],
            vapid_claims={"sub": "mailto:ops@bookstore.example"})


def notify_browser_unkeyed(subscription):
    webpush(subscription_info=subscription, data="Back in stock")
