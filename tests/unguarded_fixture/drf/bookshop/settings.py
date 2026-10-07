SECRET_KEY = "fake-bookstore-key-0000"
INSTALLED_APPS = ["rest_framework", "catalog"]
ROOT_URLCONF = "bookshop.urls"

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": ["rest_framework.authentication.TokenAuthentication"],
    "DEFAULT_PERMISSION_CLASSES": ["bookshop.permissions.ShelfTokenPermissions"],
}
