import os

EMAIL_BACKEND = "anymail.backends.sendgrid.EmailBackend"
ANYMAIL = {
    "SENDGRID_API_KEY": os.environ["SENDGRID_API_KEY"],
}
