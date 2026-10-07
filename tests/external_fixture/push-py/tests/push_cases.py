from firebase_admin import messaging


def test_send_from_a_test():
    messaging.send(messaging.Message(token="t"))
