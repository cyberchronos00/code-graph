from django.core.files.storage import default_storage


def save(name, content):
    return default_storage.save(name, content)
