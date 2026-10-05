import os

import boto3


def upload(body: bytes):
    """PutObject with the default credential chain."""
    s3 = boto3.client("s3")
    s3.put_object(Bucket="media", Key="a.txt", Body=body)


def upload_configured(body: bytes):
    s3 = boto3.client(
        "s3",
        aws_access_key_id=os.environ["AWS_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["AWS_SECRET_ACCESS_KEY"],
    )
    s3.put_object(Bucket=os.environ["BUCKET"], Key="b.txt", Body=body)


def enqueue(body: str):
    sqs = boto3.client("sqs")
    sqs.send_message(QueueUrl=os.environ["QUEUE_URL"], MessageBody=body)


def read_secret():
    sm = boto3.client("secretsmanager")
    return sm.get_secret_value(SecretId="prod/db")
