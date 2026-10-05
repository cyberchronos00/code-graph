import os

from azure.storage.blob import BlobServiceClient


def upload(data: bytes):
    client = BlobServiceClient.from_connection_string(os.environ["AZURE_STORAGE_CONNECTION_STRING"])
    client.get_container_client("docs").upload_blob("a", data)
