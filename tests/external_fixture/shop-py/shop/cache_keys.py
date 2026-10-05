"""Cache key helpers used by the shop."""
from django.core.cache import cache
import redis

r = redis.from_url("redis://cache:6379/1")

def user_profile(uid: int):
    key = f"cache:user:{uid}"
    return cache.get(key) or r.get(f"sess:{uid}")

def block_ip(ip: str):
    return cache.set(f"login:block:ip:{ip}", 1)

# Elasticsearch index for product search
INDEX = "shop_products"

def index_product(es, doc):
    es.index(index=INDEX, document=doc)
