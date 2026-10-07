import os

from azure.identity import ClientSecretCredential, DefaultAzureCredential
from azure.keyvault.secrets import SecretClient
from google.cloud import kms, secretmanager

sm = secretmanager.SecretManagerServiceClient()
keys = kms.KeyManagementServiceClient.from_service_account_file(os.environ["GOOGLE_APPLICATION_CREDENTIALS"])


def db_password():
    return sm.access_secret_version(request={"name": "projects/bookstore-prod/secrets/db-password/versions/latest"})


def seal(data):
    return keys.encrypt(request={"name": "projects/bookstore-prod/locations/eu/keyRings/shop/cryptoKeys/orders", "plaintext": data})


def api_key():
    client = SecretClient(vault_url="https://bookstore-vault.vault.azure.net", credential=DefaultAzureCredential())
    return client.get_secret("payments-api-key")


def explicit():
    cred = ClientSecretCredential(os.environ["AZURE_TENANT_ID"], os.environ["AZURE_CLIENT_ID"], os.environ["AZURE_CLIENT_SECRET"])
    client = SecretClient(vault_url=os.environ["KEY_VAULT_URL"], credential=cred)
    return client.get_secret("smtp-password")
