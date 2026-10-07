import firebase_admin
from firebase_admin import auth, credentials

firebase_admin.initialize_app(credentials.Certificate("secrets/service-account.json"))


def drop_user(uid):
    auth.delete_user(uid)
