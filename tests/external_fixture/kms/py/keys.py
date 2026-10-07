import os

import boto3

kms = boto3.client("kms", region_name="eu-west-1")


def seal_literal(data):
    return kms.encrypt(KeyId="alias/bookstore-orders", Plaintext=data)


def seal_env(data):
    return kms.encrypt(KeyId=os.environ["ORDERS_KMS_KEY_ID"], Plaintext=data)


def seal_arn(data):
    return kms.generate_data_key(KeyId="arn:aws:kms:eu-west-1:111122223333:key/1234abcd-12ab-34cd-56ef-1234567890ab", KeySpec="AES_256")


def open_explicit(blob):
    client = boto3.client("kms", aws_access_key_id=os.environ["AWS_ACCESS_KEY_ID"], aws_secret_access_key=os.environ["AWS_SECRET_ACCESS_KEY"])
    return client.decrypt(KeyId="alias/bookstore-orders", CiphertextBlob=blob)
