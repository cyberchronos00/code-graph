import boto3


def test_upload_from_tests():
    s3 = boto3.client("s3")
    s3.put_object(Bucket="from-test", Key="t", Body=b"")
