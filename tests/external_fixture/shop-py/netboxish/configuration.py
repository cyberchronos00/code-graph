DATABASE = {
    "NAME": "inventory",
    "USER": "inventory",
    "PASSWORD": "pg-fixture-literal",
    "HOST": "localhost",
    "PORT": "",
}

REDIS = {
    "tasks": {"HOST": "localhost", "PORT": 6379, "DATABASE": 0},
    "caching": {"HOST": "redis-cache.internal.example", "PORT": 6380, "DATABASE": 1},
}
