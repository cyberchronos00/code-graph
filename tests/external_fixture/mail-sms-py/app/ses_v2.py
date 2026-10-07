import boto3

sesv2 = boto3.client("sesv2", region_name="eu-west-1")


def send_v2(to):
    return sesv2.send_email(
        FromEmailAddress="shop@bookstore.example",
        Destination={"ToAddresses": [to]},
        Content={"Simple": {"Subject": {"Data": "Hi"}, "Body": {"Text": {"Data": "Hello"}}}},
    )


def verify_domain(domain):
    return boto3.client("sesv2").create_email_identity(EmailIdentity=domain)
