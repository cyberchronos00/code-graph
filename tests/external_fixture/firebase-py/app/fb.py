import os

import firebase_admin
from firebase_admin import auth, credentials, db, firestore, messaging, storage

firebase_admin.initialize_app(credentials.Certificate(os.environ["GOOGLE_APPLICATION_CREDENTIALS"]),
                              {"storageBucket": "bookstore-prod.appspot.com", "databaseURL": "https://bookstore.firebaseio.com"})
store = firestore.client()


def list_orders():
    return store.collection("orders").where("status", "==", "open").stream()


def save_review(rid):
    store.collection(os.environ["REVIEWS_COLLECTION"]).document(rid).set({"ok": True})


def who_is(token):
    return auth.verify_id_token(token)


def push(token):
    messaging.send(messaging.Message(token=token))


def stock():
    db.reference("/stock").set({"a": 1})


def upload(blob):
    storage.bucket().blob("covers/a.png").upload_from_string(blob)
