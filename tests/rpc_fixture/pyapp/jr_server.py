from jsonrpcserver import method, Success


@method
def ping():
    return Success("pong")


@method(name="math.double")
def double(x):
    return Success(x * 2)
