from flask import Flask

app = Flask(__name__)


@app.route("/api/webhooks/payments/<store>", methods=["POST"])
def payments_hook(store):
    return "ok"
