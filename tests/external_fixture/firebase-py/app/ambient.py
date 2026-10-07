import firebase_admin
from firebase_admin import credentials, firestore

firebase_admin.initialize_app(credentials.ApplicationDefault())


def list_authors():
    return firestore.client().collection("authors").get()
