import os

from google.cloud import storage


def upload(data: bytes):
    client = storage.Client()
    client.bucket("assets").blob("a.bin").upload_from_string(data)


def upload_env(data: bytes):
    storage.Client().bucket(os.environ["GCS_BUCKET"]).blob("b.bin").upload_from_string(data)
